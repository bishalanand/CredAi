"""Run the SmartDialer assignment simulation using the real Phase 13 components.

The simulation exercises:
    PredictivePacingEngine -> SafetyController -> CallAllocator -> Provider
    -> EventProcessor -> Agent/Call state machines

The mock providers use compressed timing so the assignment scenarios finish
quickly.  The requested talk-time values are still supplied to the pacing
engine as the scenario's estimated talk duration.
"""

import argparse
import asyncio
import random
import time
from collections import Counter
from dataclasses import dataclass

from app.db import SessionLocal, init_db
from app.domain.agent import Agent
from app.domain.borrower import Borrower
from app.domain.campaign import Campaign
from app.domain.enum import AgentStatus, CallStatus, DialingMode
from app.dialer import (
    CallAllocator,
    PredictivePacingEngine,
    SafetyController,
    SafetyControllerRequest,
)
from app.event_processor import EventProcessor, EventProcessingResult
from app.providers.base import ProviderInitiateCallRequest
from app.providers.mock_provider_a import MockProviderA
from app.providers.mock_provider_b import MockProviderB
from app.repositories import AgentRepository, BorrowerRepository, CallRepository, CampaignRepository
from app.state_machine.agent_state_machine import AgentStateMachine


@dataclass(frozen=True)
class Scenario:
    name: str
    answer_rate: float
    talk_time_sec: float
    provider: str
    provider_failure_rate: float = 0.0
    provider_latency_ms: float = 10.0
    provider_ring_ms: float = 30.0


SCENARIOS = (
    Scenario("A", 0.20, 120.0, "A"),
    Scenario("B", 0.50, 90.0, "A"),
    Scenario("C", 0.70, 180.0, "A"),
    # Provider B deliberately introduces failures, duplicates and possible
    # out-of-order events.  Timing is compressed for a fast simulation.
    Scenario("D", 0.50, 90.0, "B", provider_failure_rate=0.30, provider_latency_ms=20.0, provider_ring_ms=50.0),
)


class Simulation:
    def __init__(self, scenario: Scenario, agents: int, borrowers: int, seed: int):
        self.scenario = scenario
        self.agent_count = agents
        self.borrower_count = borrowers
        self.seed = seed

        random.seed(seed)
        init_db()
        self.db = SessionLocal()

        self.campaign_id = f"simulation-{scenario.name.lower()}"
        self._create_data()

        self.event_processor = EventProcessor(self.db)
        self.event_results = Counter()

        # The real event processor expects the agent to be CONNECTED before
        # a COMPLETED event.  Provider ANSWERED is therefore synchronized with
        # the agent state here, which is the orchestration layer around the
        # existing Phase 13 components.
        self.event_processor.on_call_completed(self._on_completed)
        self.event_processor.on_call_failed(self._on_failed)

        self.provider = self._create_provider()
        self.provider.on_event(self._on_provider_event)

    def _create_data(self):
        campaign = Campaign(
            id=self.campaign_id,
            name=f"Simulation Scenario {self.scenario.name}",
            dialing_mode=DialingMode.PREDICTIVE,
            active=True,
        )
        CampaignRepository(self.db).create(campaign)

        agent_repo = AgentRepository(self.db)
        for i in range(self.agent_count):
            agent_repo.create(
                Agent(id=f"sim-{self.scenario.name}-agent-{i}", status=AgentStatus.AVAILABLE)
            )

        borrower_repo = BorrowerRepository(self.db)
        for i in range(self.borrower_count):
            borrower_repo.create(
                Borrower(
                    id=f"sim-{self.scenario.name}-borrower-{i}",
                    phone_number=f"+9100000{i:05d}",
                    campaign_id=self.campaign_id,
                )
            )

    def _create_provider(self):
        if self.scenario.provider == "B":
            return MockProviderB(
                answer_rate=self.scenario.answer_rate,
                avg_setup_time_ms=self.scenario.provider_latency_ms,
                avg_ring_time_ms=self.scenario.provider_ring_ms,
                failure_rate=self.scenario.provider_failure_rate,
                duplicate_event_rate=0.20,
                out_of_order_rate=0.10,
            )

        return MockProviderA(
            answer_rate=self.scenario.answer_rate,
            avg_setup_time_ms=self.scenario.provider_latency_ms,
            avg_ring_time_ms=self.scenario.provider_ring_ms,
            failure_rate=0.0,
        )

    def _on_provider_event(self, event):
        # Keep agent lifecycle synchronized with the call lifecycle.
        agent_repo = AgentRepository(self.db)
        agent = agent_repo.get_by_id(self._call_agent_id(event.call_id))

        if agent and event.event_type == "ANSWERED":
            if agent.status == AgentStatus.DIALING:
                agent = AgentStateMachine.transition(agent, AgentStatus.CONNECTED)
                agent_repo.update(agent)

        result = self.event_processor.process_event(event)
        self.event_results[result.result.value] += 1
        self.event_results[f"event:{event.event_type}"] += 1

    def _call_agent_id(self, call_id):
        call = CallRepository(self.db).get_by_id(call_id)
        return call.agent_id if call else None

    def _on_completed(self, call):
        # EventProcessor moves CONNECTED -> WRAP_UP. Release WRAP_UP -> AVAILABLE
        # for the next dial.
        agent_repo = AgentRepository(self.db)
        borrower_repo = BorrowerRepository(self.db)

        agent = agent_repo.get_by_id(call.agent_id) if call.agent_id else None
        if agent and agent.status == AgentStatus.WRAP_UP:
            agent = AgentStateMachine.transition(agent, AgentStatus.AVAILABLE)
            agent.current_call_id = None
            agent_repo.update(agent)

        borrower = borrower_repo.get_by_id(call.borrower_id)
        if borrower:
            from app.domain.enum import BorrowerStatus
            borrower.status = BorrowerStatus.COMPLETED
            borrower.current_call_id = None
            borrower.version += 1
            borrower.update_timestamp()
            borrower_repo.update(borrower)

    def _on_failed(self, call):
        borrower_repo = BorrowerRepository(self.db)
        borrower = borrower_repo.get_by_id(call.borrower_id)
        if borrower:
            from app.domain.enum import BorrowerStatus
            borrower.status = BorrowerStatus.FAILED
            borrower.current_call_id = None
            borrower.version += 1
            borrower.update_timestamp()
            borrower_repo.update(borrower)

    def _prepare_call_for_provider(self, call):
        """Move allocator output QUEUED -> RESERVED -> INITIATED."""
        from app.domain.enum import CallStatus
        from app.state_machine.call_state_machine import CallStateMachine

        call_repo = CallRepository(self.db)

        call = call_repo.get_by_id(call.id)
        call = CallStateMachine.transition(call, CallStatus.RESERVED)
        if not call_repo.update(call):
            return None

        call = CallStateMachine.transition(call, CallStatus.INITIATED)
        call.initiated_at = call.updated_at
        if not call_repo.update(call):
            return None

        agent_repo = AgentRepository(self.db)
        agent = agent_repo.get_by_id(call.agent_id)
        if agent and agent.status == AgentStatus.RESERVED:
            agent = AgentStateMachine.transition(agent, AgentStatus.DIALING)
            agent_repo.update(agent)

        return call

    async def run(self):
        pacing = PredictivePacingEngine(self.db)
        safety = SafetyController(self.db)
        allocator = CallAllocator(self.db)

        start = time.perf_counter()

        recommendation = pacing.calculate_dial_recommendation(
            self.campaign_id,
            estimated_answer_rate=self.scenario.answer_rate,
            estimated_talk_duration_sec=self.scenario.talk_time_sec,
            estimated_setup_time_sec=self.scenario.provider_latency_ms / 1000.0,
        )

        decision = safety.evaluate_dial_request(
            SafetyControllerRequest(
                campaign_id=self.campaign_id,
                requested_dials=recommendation,
                estimated_answer_rate=self.scenario.answer_rate,
                reason=f"Scenario {self.scenario.name}",
            )
        )

        calls_started = 0
        provider_failures = 0

        # Keep one simulation batch reasonably small.  This is a demonstration,
        # not a production load generator.
        for _ in range(decision.approved_dials):
            call = allocator.allocate_call(self.campaign_id, self.scenario.provider and (
                "MockProviderB" if self.scenario.provider == "B" else "MockProviderA"
            ))
            if not call:
                break

            call = self._prepare_call_for_provider(call)
            if not call:
                break

            borrower = BorrowerRepository(self.db).get_by_id(call.borrower_id)
            request = ProviderInitiateCallRequest(
                campaign_id=self.campaign_id,
                agent_id=call.agent_id,
                borrower_id=call.borrower_id,
                borrower_phone=borrower.phone_number,
                call_id=call.id,
            )

            try:
                provider_call_id = await self.provider.initiate_call(request)
            except Exception:
                provider_failures += 1
                # Provider initiation failed before an event was emitted.
                # Release the reserved resources safely for the simulation.
                agent_repo = AgentRepository(self.db)
                agent = agent_repo.get_by_id(call.agent_id)
                if agent and agent.status in (AgentStatus.DIALING, AgentStatus.RESERVED):
                    agent = AgentStateMachine.transition(agent, AgentStatus.AVAILABLE)
                    agent.current_call_id = None
                    agent_repo.update(agent)
                continue

            call_repo = CallRepository(self.db)
            stored_call = call_repo.get_by_id(call.id)
            stored_call.provider_call_id = provider_call_id
            stored_call.version += 1
            if call_repo.update(stored_call):
                calls_started += 1

        # Wait for all mock-provider tasks to finish.  Provider B can be slower,
        # but its compressed timings still make this short.
        await asyncio.sleep(1.5 if self.scenario.provider == "B" else 1.0)

        call_repo = CallRepository(self.db)
        calls = call_repo.get_by_campaign(self.campaign_id)

        status_counts = Counter(call.status.value for call in calls)
        elapsed = time.perf_counter() - start

        connected = status_counts[CallStatus.ANSWERED.value] + status_counts[CallStatus.CONNECTED.value] + status_counts[CallStatus.COMPLETED.value]
        completed = status_counts[CallStatus.COMPLETED.value]
        failed = status_counts[CallStatus.FAILED.value]

        available_agents = AgentRepository(self.db).count_available_agents()
        utilization = ((self.agent_count - available_agents) / self.agent_count) if self.agent_count else 0.0

        result = {
            "scenario": self.scenario.name,
            "agents": self.agent_count,
            "borrowers": self.borrower_count,
            "recommendation": recommendation,
            "safety_decision": decision.decision.value,
            "approved_dials": decision.approved_dials,
            "calls_started": calls_started,
            "connected_or_completed": connected,
            "completed": completed,
            "failed": failed + provider_failures,
            "provider_failures": provider_failures,
            "available_agents_end": available_agents,
            "estimated_utilization": utilization,
            "event_results": dict(self.event_results),
            "elapsed_sec": elapsed,
        }
        self.db.close()
        return result


async def run_all(agents: int, borrowers: int, seed: int):
    results = []
    for scenario in SCENARIOS:
        sim = Simulation(scenario, agents, borrowers, seed)
        results.append(await sim.run())
    return results


def print_result(result):
    print("\n" + "=" * 72)
    print(f"SCENARIO {result['scenario']}")
    print("=" * 72)
    print(f"Agents:                  {result['agents']}")
    print(f"Borrowers:               {result['borrowers']}")
    print(f"Pacing recommendation:   {result['recommendation']}")
    print(f"Safety decision:         {result['safety_decision']}")
    print(f"Safety approved dials:   {result['approved_dials']}")
    print(f"Calls started:           {result['calls_started']}")
    print(f"Completed:               {result['completed']}")
    print(f"Failed:                  {result['failed']}")
    print(f"Provider failures:       {result['provider_failures']}")
    print(f"Estimated utilization:   {result['estimated_utilization']:.2%}")
    print(f"Available agents at end: {result['available_agents_end']}")
    print(f"Runtime:                 {result['elapsed_sec']:.3f}s")
    print("Event results:")
    for key, value in sorted(result["event_results"].items()):
        print(f"  {key:25s} {value}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", type=int, default=20)
    parser.add_argument("--borrowers", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    print("SMARTDIALER SIMULATION")
    print("Using actual Phase 13 components with compressed mock-provider timing.")

    results = asyncio.run(run_all(args.agents, args.borrowers, args.seed))
    for result in results:
        print_result(result)


if __name__ == "__main__":
    main()
