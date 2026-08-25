from enum import Enum

class AgentStatus(str, Enum):
    OFFLINE = "OFFLINE"
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    DIALING = "DIALING"
    CONNECTED = "CONNECTED"
    WRAP_UP = "WRAP_UP"
    PAUSED = "PAUSED"


class BorrowerStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    IN_CALL = "IN_CALL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CallStatus(str, Enum):
    QUEUED = "QUEUED"
    RESERVED = "RESERVED"
    INITIATED = "INITIATED"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    CONNECTED = "CONNECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DialingMode(str, Enum):
    PROGRESSIVE = "PROGRESSIVE"
    PREDICTIVE = "PREDICTIVE"
    
#Enum protects from state related bugs