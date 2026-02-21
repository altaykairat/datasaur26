"""Script to safely migrate the database schema for Multi-Branch Routing."""
import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database.connection import engine, get_db
from database.models import Manager, Office, Ticket, RoundRobinState


def upgrade_offices(db):
    """Update Offices table: Drop unique constraint on city, add name."""
    print("[MIGRATE] Upgrading `offices` table...")
    
    # 1. Add `name` column
    try:
        db.execute(text("ALTER TABLE offices ADD COLUMN IF NOT EXISTS name VARCHAR(150)"))
        print("  - Added column `name` to `offices`.")
    except Exception as e:
        print(f"  - Column `name` might already exist: {e}")

    # 2. Drop UNIQUE constraint on `city`
    try:
        # The constraint name might vary depending on SQLAlchemy auto-generation.
        # Usually, it's something like ix_offices_city or offices_city_key
        # We try dropping the most common default names.
        db.execute(text("ALTER TABLE offices DROP CONSTRAINT IF EXISTS offices_city_key"))
        print("  - Dropped unique constraint `offices_city_key` on `city`.")
    except Exception as e:
        print(f"  - Constraint `offices_city_key` could not be dropped: {e}")
        
    # We populate the `name` column with the address as a default for existing ones
    offices = db.query(Office).all()
    for o in offices:
        if not o.name:
            o.name = f"{o.city}, {o.address}" if o.address else f"{o.city} Branch"
    
    db.flush()


def upgrade_managers(db):
    """Update Managers table: add office_id and link to Offices."""
    print("[MIGRATE] Upgrading `managers` table...")
    
    # 1. Add `office_id` column
    try:
        db.execute(text("ALTER TABLE managers ADD COLUMN IF NOT EXISTS office_id INTEGER REFERENCES offices(id)"))
        print("  - Added column `office_id` to `managers`.")
    except Exception as e:
        print(f"  - Column `office_id` might already exist: {e}")
        
    # 2. Backfill `office_id` based on `office_location`
    managers = db.query(Manager).all()
    for m in managers:
        if not m.office_id and m.office_location:
            office = db.query(Office).filter(Office.city == m.office_location).first()
            if office:
                m.office_id = office.id
    
    db.flush()


def upgrade_tickets(db):
    """Update Tickets table: add routed_branch_id, alternative_branches."""
    print("[MIGRATE] Upgrading `tickets` table...")
    
    # 1. Add `routed_branch_id` column
    try:
        db.execute(text("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS routed_branch_id INTEGER REFERENCES offices(id)"))
        print("  - Added column `routed_branch_id` to `tickets`.")
    except Exception as e:
        print(f"  - Column `routed_branch_id` might already exist: {e}")

    # 2. Add `alternative_branches` column
    try:
        # In PostgreSQL JSON type is JSON for SQLAlchemy JSON
        db.execute(text("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS alternative_branches JSON"))
        print("  - Added column `alternative_branches` to `tickets`.")
    except Exception as e:
        try:
           db.execute(text("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS alternative_branches TEXT"))
        except Exception:
           pass
        print(f"  - Column `alternative_branches` might already exist: {e}")


def upgrade_rr_state(db):
    """Update RoundRobinState table: swap office_city for office_id."""
    print("[MIGRATE] Upgrading `rr_state` table...")
    
    # 1. Add `office_id` column
    try:
        db.execute(text("ALTER TABLE rr_state ADD COLUMN IF NOT EXISTS office_id INTEGER"))
        print("  - Added column `office_id` to `rr_state`.")
    except Exception as e:
        print(f"  - Column `office_id` might already exist: {e}")
        
    # 2. Backfill
    states = db.query(RoundRobinState).all()
    # We use db.execute because office_city field was removed from the model definition
    res = db.execute(text("SELECT id, office_city FROM rr_state"))
    for row in res:
        state_id, city = row[0], row[1]
        if city:
             office = db.query(Office).filter(Office.city == city).first()
             if office:
                 db.execute(text(f"UPDATE rr_state SET office_id = {office.id} WHERE id = {state_id}"))

    # 3. Drop `office_city`
    try:
        db.execute(text("ALTER TABLE rr_state DROP COLUMN IF EXISTS office_city"))
        print("  - Dropped column `office_city` from `rr_state`.")
    except Exception as e:
        print(f"  - Column `office_city` could not be dropped: {e}")


def run_migration():
    """Execute all migrations."""
    print("Starting Multi-Branch Database Migration...")
    
    with get_db() as db:
        upgrade_offices(db)
        upgrade_managers(db)
        upgrade_tickets(db)
        upgrade_rr_state(db)
        # Commit happens automatically via get_db context manager
        
    print("Migration Complete.")


if __name__ == "__main__":
    run_migration()
