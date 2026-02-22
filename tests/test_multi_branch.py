import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.connection import get_db
from database.models import Office, Manager
from engine.router import TicketRouter

def setup_test_data(db):
    # Add a fake second Almaty branch very far from the center
    # Center = 43.2380, 76.9457 (Esentai Tower)
    # Fake Branch B = 43.1500, 76.8500
    fake_branch_b = db.query(Office).filter(Office.name == "Алматы, Fake Branch B").first()
    if not fake_branch_b:
        fake_branch_b = Office(
            city="Алматы",
            name="Алматы, Fake Branch B",
            address="Fake Street 123",
            lat=43.1500,
            lon=76.8500
        )
        db.add(fake_branch_b)
        db.flush()
        
        # Add a manager to the fake branch
        fake_manager = Manager(
            name="Test Manager B",
            role="Главный специалист",
            skills=["VIP", "ENG", "KZ"],
            office_location="Алматы",
            office_id=fake_branch_b.id,
            current_load=0,
            is_active=True
        )
        db.add(fake_manager)
        db.commit()

def test_routing():
    with get_db() as db:
        setup_test_data(db)
        
    router = TicketRouter(ai_mode="deepseek")
    
    # Test 1: Ticket extremely far from center, should route to Fake Branch B
    # AI description explicitly says they are on Fake Street
    desc1 = "У меня проблема. Живу в Алматы, Fake Street 123. Верните деньги!"
    print("--- Test 1 (Expected: Fake Branch B) ---")
    res1 = router.route_single_ticket(desc1, segment="Mass", client_city="Алматы")
    print(f"Routed Branch: {res1.get('routing_trace', {}).get('geo_decision', {}).get('branch_name')}")
    print(f"Distance: {res1.get('routing_trace', {}).get('geo_decision', {}).get('distance_km')} km")
    print(f"Alternatives: {res1.get('alternative_branches')}")
    
    # Test 2: Ticket exactly at Esentai Tower, should route to Esentai (Branch A)
    desc2 = "У меня проблема. Живу в Алматы, пр-т Аль-Фараби, 77/7. Верните деньги!"
    print("\n--- Test 2 (Expected: Esentai Tower Branch A) ---")
    res2 = router.route_single_ticket(desc2, segment="Mass", client_city="Алматы")
    print(f"Routed Branch: {res2.get('routing_trace', {}).get('geo_decision', {}).get('branch_name')}")
    print(f"Distance: {res2.get('routing_trace', {}).get('geo_decision', {}).get('distance_km')} km")
    print(f"Alternatives: {res2.get('alternative_branches')}")

    # Test 3: Ticket from a non-office city (e.g. Тургень, far from Almaty)
    desc3 = "Живу в поселке Тургень. Помогите с возвратом."
    print("\n--- Test 3 (Expected: Any Almaty branch based on closest distance) ---")
    res3 = router.route_single_ticket(desc3, segment="Mass", client_city="Тургень")
    print(f"Routed Branch: {res3.get('routing_trace', {}).get('geo_decision', {}).get('branch_name')}")
    print(f"Distance: {res3.get('routing_trace', {}).get('geo_decision', {}).get('distance_km')} km")
    print(f"Alternatives: {res3.get('alternative_branches')}")

    # Test 4: Skill fallback within the same city
    desc4 = "I need to speak to a senior manager immediately. I am located at Fake Street 123 in Almaty."
    print("\n--- Test 4 (Expected: Almaty Fake Branch B, due to strict skill match for ENG + VIP/Senior) ---")
    res4 = router.route_single_ticket(desc4, segment="VIP", client_city="Алматы")
    print(f"Routed Branch: {res4.get('routing_trace', {}).get('geo_decision', {}).get('branch_name')}")
    print(f"Assigned Manager: {res4.get('assigned_manager_name')}")
    print(f"Fallback Used: {res4.get('routing_trace', {}).get('skill_filter', {}).get('fallback_used')}")
    print(f"Applied Rules: {res4.get('routing_trace', {}).get('skill_filter', {}).get('applied_rules')}")

if __name__ == "__main__":
    test_routing()
