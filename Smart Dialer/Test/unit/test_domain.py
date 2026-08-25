from app.domain import (
    Agent,
    AgentStatus,
    Borrower,
    BorrowerStatus,
    Call,
    CallStatus,
    Campaign,
    DialingMode,
)


def test_agent_defaults_to_offline():
    agent = Agent(id="agent-1")

    assert agent.status == AgentStatus.OFFLINE
    assert agent.current_call_id is None
    assert agent.version == 0


def test_borrower_defaults_to_available():
    borrower = Borrower(
        id="borrower-1",
        phone_number="+911234567890",
    )

    assert borrower.status == BorrowerStatus.AVAILABLE
    assert borrower.current_call_id is None


def test_call_defaults_to_queued():
    call = Call(
        id="call-1",
        campaign_id="campaign-1",
        borrower_id="borrower-1",
    )

    assert call.status == CallStatus.QUEUED
    assert call.agent_id is None


def test_campaign_defaults_to_progressive():
    campaign = Campaign(
        id="campaign-1",
        name="Test Campaign",
    )

    assert campaign.dialing_mode == DialingMode.PROGRESSIVE
    assert campaign.active is False


def test_predictive_campaign():
    campaign = Campaign(
        id="campaign-1",
        name="Predictive Campaign",
        dialing_mode=DialingMode.PREDICTIVE,
    )

    assert campaign.dialing_mode == DialingMode.PREDICTIVE