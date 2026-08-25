"""Basic load test for the SmartDialer Phase 13 database/repository layer.

The test intentionally uses the real SQLAlchemy models and repositories.
It measures database/query behaviour at 100, 1,000 and 10,000 agents and
performs a small allocation batch through the real CallAllocator.

This is a prototype load test, not a production benchmark.
"""

import argparse
import statistics
import time
from dataclasses import dataclass

from app.db import SessionLocal, init_db
from app.domain.agent import Agent
from app.domain.borrower import Borrower
from app.domain.campaign import Campaign
from app.domain.enum import AgentStatus, DialingMode
from app.models import AgentModel, BorrowerModel
from app.repositories import AgentRepository, BorrowerRepository, CampaignRepository
from app.dialer import CallAllocator


@dataclass
class LoadResult:
    agents: int
    setup_seconds: float
    count_seconds: float
    fetch_seconds: float
    allocation_seconds: float
    allocated_calls: int
    allocation_batch: int


def seed_database(agent_count: int, borrower_count: int, campaign_id: str):
    """Seed large populations efficiently using SQLAlchemy bulk inserts."""
    session = SessionLocal()

    campaign = Campaign(
        id=campaign_id,
        name=f"Load Test {agent_count}",
        dialing_mode=DialingMode.PROGRESSIVE,
        active=True,
    )
    CampaignRepository(session).create(campaign)

    # Bulk inserts are deliberate: the benchmark should measure the system
    # operations rather than spend most of the time on one commit per row.
    session.bulk_save_objects([
        AgentModel(
            id=f"load-agent-{i}",
            status=AgentStatus.AVAILABLE,
            version=0,
        )
        for i in range(agent_count)
    ])

    session.bulk_save_objects([
        BorrowerModel(
            id=f"load-borrower-{i}",
            phone_number=f"+9100000{i:06d}",
            campaign_id=campaign_id,
        )
        for i in range(borrower_count)
    ])

    session.commit()
    session.close()


def run_single(agent_count: int, allocation_batch: int):
    init_db()
    campaign_id = f"load-{agent_count}"
    borrower_count = max(agent_count, allocation_batch)

    start = time.perf_counter()
    seed_database(agent_count, borrower_count, campaign_id)
    setup_seconds = time.perf_counter() - start

    session = SessionLocal()
    agent_repo = AgentRepository(session)

    start = time.perf_counter()
    available_count = agent_repo.count_available_agents()
    count_seconds = time.perf_counter() - start

    if available_count != agent_count:
        raise RuntimeError(
            f"Expected {agent_count} available agents, got {available_count}"
        )

    start = time.perf_counter()
    agents = agent_repo.get_available_agents(limit=min(100, agent_count))
    fetch_seconds = time.perf_counter() - start

    if len(agents) == 0:
        raise RuntimeError("Repository returned no available agents")

    allocator = CallAllocator(session)

    start = time.perf_counter()
    allocated = 0
    for _ in range(min(allocation_batch, agent_count)):
        call = allocator.allocate_call(campaign_id, "MockProviderA")
        if call is None:
            break
        allocated += 1
    allocation_seconds = time.perf_counter() - start

    session.close()

    return LoadResult(
        agents=agent_count,
        setup_seconds=setup_seconds,
        count_seconds=count_seconds,
        fetch_seconds=fetch_seconds,
        allocation_seconds=allocation_seconds,
        allocated_calls=allocated,
        allocation_batch=min(allocation_batch, agent_count),
    )


def print_results(results):
    print("\n" + "=" * 92)
    print("SMARTDIALER LOAD TEST")
    print("=" * 92)
    print(
        f"{'Agents':>10} | {'Setup(s)':>10} | {'Count(s)':>10} | "
        f"{'Fetch(s)':>10} | {'Alloc(s)':>10} | {'Allocated':>10}"
    )
    print("-" * 92)

    for r in results:
        print(
            f"{r.agents:>10,} | "
            f"{r.setup_seconds:>10.4f} | "
            f"{r.count_seconds:>10.4f} | "
            f"{r.fetch_seconds:>10.4f} | "
            f"{r.allocation_seconds:>10.4f} | "
            f"{r.allocated_calls:>10,}"
        )

    print("\nInterpretation:")
    print("- Count/Fetch growth shows repository query scaling.")
    print("- Allocation time includes the real CallAllocator and multiple DB commits.")
    print("- If allocation time grows much faster than query time, DB write/commit")
    print("  contention is a likely bottleneck.")
    print("- Use the observed first bottleneck in the assignment's scaling discussion.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=[100, 1000, 10000],
        help="Agent counts to test",
    )
    parser.add_argument(
        "--allocation-batch",
        type=int,
        default=25,
        help="Number of real CallAllocator operations per size",
    )
    args = parser.parse_args()

    results = []
    for size in args.sizes:
        print(f"Running load test for {size:,} agents...")
        results.append(run_single(size, args.allocation_batch))

    print_results(results)


if __name__ == "__main__":
    main()
