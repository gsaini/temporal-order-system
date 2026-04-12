from dataclasses import dataclass


@dataclass
class Order:
    """Represents an e-commerce order flowing through the pipeline."""

    order_id: str
    item: str
    amount: float
    customer_id: str = "CUST-DEFAULT"


@dataclass
class OrderStatus:
    """Tracks the current state of an order workflow."""

    order_id: str
    step: str
    completed: bool = False
    error: str = ""
