"""Engine Subpackage: Load Balancer

Encapsulates round-robin load load balancing logic via DB-level locks,
ensuring equal distribution of tickets to filtered manager pools.
"""
from typing import Optional
from database.models import Manager, RoundRobinState
from utils.logging import get_safe_logger

logger = get_safe_logger("load_balancer")

class LoadBalancer:
    """Handles distributing workloads evenly across matched managers."""

    @staticmethod
    def balance(candidates: list[Manager], branch_id: Optional[int], db) -> tuple[Optional[Manager], dict]:
        """
        Pick the best manager from candidates using top-2 + round robin.

        Returns (manager, lb_trace).
        """
        lb_trace = {
            "candidate_ids": [],
            "candidate_loads": [],
            "rr_pointer": None,
            "chosen_manager_id": None,
            "workload_at_assignment": None,
            "invalid_load_coerced": False,
        }

        if not candidates:
            return None, lb_trace

        # Validate and coerce load values
        for m in candidates:
            if getattr(m, 'current_load', None) is None or m.current_load < 0:
                m.current_load = 9999
                lb_trace["invalid_load_coerced"] = True
                logger.warning(f"Manager {m.id} had invalid load, coerced to 9999")

        # Sort by (current_load, id) for determinism
        sorted_candidates = sorted(candidates, key=lambda m: (getattr(m, 'current_load', 0), m.id))

        lb_trace["candidate_ids"] = [m.id for m in sorted_candidates]
        lb_trace["candidate_loads"] = [getattr(m, 'current_load', 0) for m in sorted_candidates]

        if len(sorted_candidates) == 1:
            chosen = sorted_candidates[0]
            lb_trace["chosen_manager_id"] = chosen.id
            lb_trace["workload_at_assignment"] = getattr(chosen, 'current_load', 0)
            return chosen, lb_trace

        # Take top 2 with minimal workload
        top_two = sorted_candidates[:2]
        candidate_key = f"{min(top_two[0].id, top_two[1].id)}_{max(top_two[0].id, top_two[1].id)}"

        # Look up or create RR state with row-level locking to prevent concurrency races
        rr_office_id = branch_id if branch_id is not None else 0
        rr = db.query(RoundRobinState).filter(
            RoundRobinState.office_id == rr_office_id,
            RoundRobinState.candidate_key == candidate_key,
        ).with_for_update().first()

        if rr is None:
            rr = RoundRobinState(
                office_id=rr_office_id,
                candidate_key=candidate_key,
                pointer=0,
            )
            db.add(rr)
            db.flush()

        # Select based on pointer
        chosen = top_two[rr.pointer % 2]
        lb_trace["rr_pointer"] = rr.pointer
        lb_trace["chosen_manager_id"] = chosen.id
        lb_trace["workload_at_assignment"] = getattr(chosen, 'current_load', 0)

        # Increment pointer atomically
        rr.pointer += 1
        db.flush()

        return chosen, lb_trace
