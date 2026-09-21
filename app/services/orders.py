"""Order operations: placing, paying and cancelling purchases."""
from datetime import datetime
from typing import Dict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Member, MemberTier, Order, OrderItem, OrderStatus
from app.schemas import OrderCreate
from app.services.members import ensure_can_access_restricted, get_member

# Percentage discount granted by each membership tier.
TIER_DISCOUNT_PERCENT: Dict[str, int] = {
    MemberTier.APPRENTICE.value: 0,
    MemberTier.ADEPT.value: 5,
    MemberTier.MASTER.value: 10,
    MemberTier.SUPREME.value: 15,
}

# Extra discount when the total quantity across all items reaches the threshold.
BULK_QUANTITY_THRESHOLD = 10
BULK_DISCOUNT_PERCENT = 5


def calculate_discount_percent(member: Member, total_quantity: int) -> int:
    """Tier discount, plus the bulk discount when total quantity >= threshold."""
    discount = TIER_DISCOUNT_PERCENT.get(member.tier, 0)
    if total_quantity >= BULK_QUANTITY_THRESHOLD:
        discount += BULK_DISCOUNT_PERCENT
    return discount


def create_order(db: Session, data: OrderCreate, now: datetime) -> Order:
    """Place a pending order and reserve stock.

    Checks, in order (422 for empty items / bad quantity / duplicate books is done by the schema):
    1. 404 member not found; 404 any book not found
    2. 403 any book restricted and member tier below master
    3. 409 any book has insufficient stock (all-or-nothing: nothing is changed)
    Then stock is decremented for every item and prices are snapshotted.
    Pricing: discount_cents = subtotal * percent // 100; total = subtotal - discount.
    """
    from app.models import Book

    # 1. Load member (404 if missing).
    member = get_member(db, data.member_id)

    # 1b. Load each book (404 if any missing).
    books: Dict[int, Book] = {}
    for item in data.items:
        book = db.get(Book, item.book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"Book {item.book_id} not found")
        books[item.book_id] = book

    # 2. Check restricted access for any restricted book.
    for book in books.values():
        if book.restricted:
            ensure_can_access_restricted(member)
            break  # one check is enough; all books share the same member

    # 3. Check stock for every item (all-or-nothing; nothing changes on failure).
    for item in data.items:
        book = books[item.book_id]
        if book.stock < item.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for book {item.book_id}",
            )

    # 4 & 5. Decrement stock and build order items; compute pricing.
    total_quantity = sum(item.quantity for item in data.items)
    order_items = []
    subtotal_cents = 0
    for item in data.items:
        book = books[item.book_id]
        book.stock -= item.quantity
        line_total = book.price_cents * item.quantity
        subtotal_cents += line_total
        order_items.append(
            OrderItem(
                book_id=item.book_id,
                quantity=item.quantity,
                unit_price_cents=book.price_cents,
            )
        )

    discount_percent = calculate_discount_percent(member, total_quantity)
    discount_cents = subtotal_cents * discount_percent // 100
    total_cents = subtotal_cents - discount_cents

    # 6. Persist the order.
    order = Order(
        member_id=data.member_id,
        status=OrderStatus.PENDING.value,
        subtotal_cents=subtotal_cents,
        discount_percent=discount_percent,
        discount_cents=discount_cents,
        total_cents=total_cents,
        created_at=now,
        items=order_items,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def get_order(db: Session, order_id: int) -> Order:
    """Return an order by id, or raise 404."""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def pay_order(db: Session, order_id: int) -> Order:
    """Mark a pending order as paid. 404 if missing; 409 if not pending."""
    order = get_order(db, order_id)
    if order.status != OrderStatus.PENDING.value:
        raise HTTPException(status_code=409, detail=f"Cannot pay an order that is {order.status}")
    order.status = OrderStatus.PAID.value
    db.commit()
    db.refresh(order)
    return order


def cancel_order(db: Session, order_id: int) -> Order:
    """Cancel a pending order and restore the reserved stock. 404 if missing; 409 if not pending."""
    order = get_order(db, order_id)
    if order.status != OrderStatus.PENDING.value:
        raise HTTPException(status_code=409, detail=f"Cannot cancel an order that is {order.status}")
    # Restore stock for every item in the order.
    for item in order.items:
        item.book.stock += item.quantity
    order.status = OrderStatus.CANCELLED.value
    db.commit()
    db.refresh(order)
    return order
