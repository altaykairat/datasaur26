"""Seed the database with managers.csv and business_units.csv data."""
import os
import sys
import csv

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from database.connection import engine, get_db  # noqa: E402
from database.models import Base, Manager, Office, User, UserRole, RoundRobinState  # noqa: E402
from utils.auth import hash_password  # noqa: E402

# Hardcoded coordinates for Kazakhstan cities (from offices)
CITY_COORDS = {
    "Актау": (43.6353, 51.1700),
    "Актобе": (50.2839, 57.1670),
    "Алматы": (43.2380, 76.9457),
    "Астана": (51.1694, 71.4491),
    "Атырау": (47.1065, 51.9228),
    "Караганда": (49.8047, 73.1094),
    "Кокшетау": (53.2833, 69.3833),
    "Костанай": (53.2144, 63.6246),
    "Кызылорда": (44.8488, 65.5228),
    "Павлодар": (52.2873, 76.9674),
    "Петропавловск": (54.8753, 69.1628),
    "Тараз": (42.9000, 71.3667),
    "Уральск": (51.2333, 51.3667),
    "Усть-Каменогорск": (49.9536, 82.6131),
    "Шымкент": (42.3200, 69.5967),
}

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _clean_key(key: str) -> str:
    """Strip BOM and whitespace from CSV column keys."""
    return key.strip().lstrip("\ufeff").strip()


def _clean_row(row: dict) -> dict:
    """Clean all keys in a CSV row by removing BOM and extra whitespace."""
    return {_clean_key(k): v for k, v in row.items()}


def seed_managers(db):
    """Load managers from managers.csv."""
    csv_path = os.path.join(PROJECT_ROOT, "input", "managers.csv")
    if not os.path.exists(csv_path):
        print(f"[WARN] managers.csv not found at {csv_path}")
        return 0

    count = 0
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = _clean_row(row)
            name = row.get("ФИО", "").strip()
            if not name:
                continue
            role = row.get("Должность", "").strip()
            office = row.get("Офис", "").strip()
            skills_raw = row.get("Навыки", "").strip()
            load_raw = row.get("Количество обращений в работе", "0").strip()

            skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
            current_load = int(load_raw) if load_raw.isdigit() else 0

            existing = db.query(Manager).filter(Manager.name == name).first()
            if existing:
                continue

            manager = Manager(
                name=name,
                role=role,
                skills=skills,
                office_location=office,
                current_load=current_load,
                is_active=True,
            )
            db.add(manager)
            count += 1

    db.flush()
    print(f"[SEED] Loaded {count} managers.")
    return count


def seed_offices(db):
    """Load offices from business_units.csv with hardcoded coordinates."""
    csv_path = os.path.join(PROJECT_ROOT, "input", "business_units.csv")
    if not os.path.exists(csv_path):
        print(f"[WARN] business_units.csv not found at {csv_path}")
        return 0

    count = 0
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = _clean_row(row)
            city = row.get("Офис", "").strip()
            address = row.get("Адрес", "").strip()
            if not city:
                continue

            existing = db.query(Office).filter(Office.city == city).first()
            if existing:
                continue

            coords = CITY_COORDS.get(city, (51.1694, 71.4491))  # Default to Astana
            office = Office(
                city=city,
                address=address,
                lat=coords[0],
                lon=coords[1],
            )
            db.add(office)
            count += 1

    db.flush()
    print(f"[SEED] Loaded {count} offices.")
    return count


def seed_admin(db):
    """Create a default admin user."""
    existing = db.query(User).filter(User.username == "admin").first()
    if existing:
        print("[SEED] Admin user already exists.")
        return

    admin = User(
        username="admin",
        password_hash=hash_password("admin"),
        role=UserRole.ADMIN.value,
    )
    db.add(admin)
    db.flush()
    print("[SEED] Created default admin user (admin/admin).")


def seed_all():
    """Create all tables and seed the database."""
    print("[SEED] Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("[SEED] Tables created.")

    with get_db() as db:
        seed_offices(db)
        seed_managers(db)
        seed_admin(db)
    print("[SEED] Done!")


if __name__ == "__main__":
    seed_all()
