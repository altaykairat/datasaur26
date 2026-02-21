"""SQLAlchemy ORM models for FIRE."""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, JSON, ForeignKey,
    DateTime, Float, Enum as SAEnum, Boolean
)
from sqlalchemy.orm import declarative_base, relationship
import enum

Base = declarative_base()


class UserRole(str, enum.Enum):
    CUSTOMER = "customer"
    MANAGER = "manager"
    ADMIN = "admin"


class TicketStatus(str, enum.Enum):
    NEW = "New"
    INGESTED = "Ingested"
    QUEUED = "Queued"
    ENRICHING = "Enriching"
    ENRICHED = "Enriched"
    ROUTING = "Routing"
    ASSIGNED = "Assigned"
    CLOSED = "Closed"
    ENRICH_FAILED = "EnrichFailed"
    ROUTING_FAILED = "RoutingFailed"
    DEAD_LETTER = "DeadLetter"
    SPAM = "Spam"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default=UserRole.CUSTOMER.value)
    manager_id = Column(Integer, ForeignKey("managers.id"), nullable=True)

    manager = relationship("Manager", back_populates="user_account")
    tickets = relationship("Ticket", back_populates="customer", foreign_keys="Ticket.customer_id")

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}', role='{self.role}')>"


class Manager(Base):
    __tablename__ = "managers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    role = Column(String(100), nullable=False)  # Специалист, Ведущий специалист, Главный специалист
    skills = Column(JSON, nullable=False, default=list)  # ["VIP", "ENG", "KZ"]
    office_location = Column(String(100), nullable=True) # Legacy city name
    office_id = Column(Integer, ForeignKey("offices.id"), nullable=True) # Specific branch ID
    current_load = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)

    user_account = relationship("User", back_populates="manager", uselist=False)
    office = relationship("Office", back_populates="managers")
    assigned_tickets = relationship("Ticket", back_populates="assigned_manager")

    def __repr__(self):
        return f"<Manager(id={self.id}, name='{self.name}', office='{self.office_location}')>"


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    client_guid = Column(String(100), nullable=True)  # From CSV batch uploads
    correlation_id = Column(String(50), nullable=True)  # Unique processing trace ID
    description = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default=TicketStatus.NEW.value)
    assigned_manager_id = Column(Integer, ForeignKey("managers.id"), nullable=True)
    ai_analysis_json = Column(JSON, nullable=True)
    segment = Column(String(50), nullable=True)  # VIP, Mass, Priority
    client_city = Column(String(100), nullable=True)
    client_address = Column(Text, nullable=True)
    office_rule = Column(String(100), nullable=True)  # How office was selected
    routed_branch_id = Column(Integer, ForeignKey("offices.id"), nullable=True) # The exact assigned branch
    alternative_branches = Column(JSON, nullable=True) # List of fallback branches
    routing_trace = Column(JSON, nullable=True)  # Full audit trail of routing decisions
    flags = Column(JSON, nullable=True)  # Boolean flags dict
    workload_at_assignment = Column(Integer, nullable=True)  # Manager load when assigned
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    customer = relationship("User", back_populates="tickets", foreign_keys=[customer_id])
    assigned_manager = relationship("Manager", back_populates="assigned_tickets")
    routed_branch = relationship("Office", foreign_keys=[routed_branch_id])

    def __repr__(self):
        return f"<Ticket(id={self.id}, status='{self.status}')>"


class Office(Base):
    __tablename__ = "offices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    city = Column(String(100), nullable=False) # Removed unique=True
    name = Column(String(150), unique=True, nullable=True) # Specific branch name
    address = Column(Text, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)

    managers = relationship("Manager", back_populates="office")

    def __repr__(self):
        return f"<Office(city='{self.city}', lat={self.lat}, lon={self.lon})>"


class RoundRobinState(Base):
    """Persisted round-robin pointer per office + candidate pair."""
    __tablename__ = "rr_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    office_id = Column(Integer, nullable=False) # Changed from city to branch ID
    candidate_key = Column(String(50), nullable=False)  # e.g. "12_34" sorted manager IDs
    pointer = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<RRState(office='{self.office_city}', key='{self.candidate_key}', ptr={self.pointer})>"
