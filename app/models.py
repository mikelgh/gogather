"""数据模型：方案、行动项、时间线、预算、房间分配。"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    BOOKING = "booking"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"


class ActionType(str, Enum):
    HOTEL = "hotel"
    TRANSPORT = "transport"
    ACTIVITY = "activity"
    RESTAURANT = "restaurant"


class Recommendation(BaseModel):
    name: str
    description: str = ""
    price: Optional[str] = None
    booking_link: Optional[str] = None
    rating: Optional[str] = None
    notes: str = ""


class TimelineSlot(BaseModel):
    time_range: str
    title: str
    action_item_id: str = ""
    location: str = ""
    notes: str = ""


class BudgetItem(BaseModel):
    category: str
    description: str
    estimated_cost: str
    is_per_person: bool = True
    action_item_id: Optional[str] = None


class RoomAllocation(BaseModel):
    room_type: str
    count: int
    price_per_night: Optional[str] = None
    hotel_name: str
    hotel_link: Optional[str] = None
    notes: str = ""


class ActionItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: ActionType
    title: str
    description: str = ""
    recommendations: list[Recommendation] = []
    estimated_cost: Optional[str] = None
    time_slot: Optional[str] = None
    assignee: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    booking_link: Optional[str] = None
    priority: str = "medium"


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    phase: str = "planning"

    original_input: str = ""
    conversation: list[dict] = []
    params: dict = {}

    summary: str = ""
    action_items: list[ActionItem] = []
    timeline: list[TimelineSlot] = []
    budget: list[BudgetItem] = []
    room_allocation: list[RoomAllocation] = []
    budget_per_person: Optional[str] = None

    share_code: Optional[str] = None
    members: list[dict] = []

    markdown: Optional[str] = None
    warnings: list[str] = []
    used_llm: bool = False
