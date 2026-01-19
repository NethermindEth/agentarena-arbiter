"""
Category utilities for finding categorization.
Provides category enumeration and inference functions.
"""
from enum import Enum


class CategoryEnum(str, Enum):
    """Category enumeration for security findings."""
    REENTRANCY = "Reentrancy"
    ACCESS_CONTROL = "Access Control"
    INTEGER_OVERFLOW_UNDERFLOW = "Integer Overflow/Underflow"
    DENIAL_OF_SERVICE = "Denial of Service"
    UNCHECKED_CALL = "Unchecked Call"
    FRONT_RUNNING = "Front Running"
    CONFIG_DEPENDENT = "Config Dependent"
    BUSINESS_LOGIC = "Business Logic"
    PRECISION_LOSS = "Precision Loss"
    CENTRALIZATION_RISK = "Centralization Risk"
    OTHER = "Other"


class CategoryUtils:
    """Category utility functions for finding categorization."""
    
    @classmethod
    def validate(cls, value) -> CategoryEnum:
        """
        Validate and convert a value to a Category enum.
        
        Args:
            value: The value to validate. Can be a Category enum or a string.
            
        Returns:
            Category: A valid Category enum instance.
        """
        # If already a Category enum, return it directly
        if isinstance(value, CategoryEnum):
            return value
        
        # Otherwise, convert string to Category enum
        try:
            return CategoryEnum(value)
        except ValueError:
            # If not found, return OTHER
            return CategoryEnum.OTHER
    
    @classmethod
    def get_category_description(cls, category: CategoryEnum) -> str:
        """
        Get category description.
        """
        description = {
            CategoryEnum.REENTRANCY: "External calls (e.g., call, transfer, send, calling an interface, etc.) that are made **before** critical state changes or validations occur, allowing re-entrant execution of the function or contract (If the external function being calls triggers a call that results in this contract or function being called again it'll keep executing because the state change is not yet applied or validations occur later).",
            CategoryEnum.ACCESS_CONTROL: "Unauthorized actors can perform sensitive actions due to missing or misconfigured permission checks (e.g., onlyOwner, role-based access, or require statements tied to user identity or privileges). Access Control findings must involve improper restrictions on who can directly execute or indirectly trigger the execution of a sensitive action.",
            CategoryEnum.INTEGER_OVERFLOW_UNDERFLOW: "Arithmetic operations (addition, subtraction, multiplication) do not safely handle numeric boundaries, leading to unexpected wrapping behavior that can affect logic.",
            CategoryEnum.DENIAL_OF_SERVICE: "An attacker (or sometimes a misuser) can cause permanent or prolonged unavailability of contract functionality. Common causes include gas exhaustion, locking key resources (like storage slots or queues), triggering reverts or failed conditions, or situations where critical operations become inaccessible or unusable.",
            CategoryEnum.UNCHECKED_CALL: "Using low-level calls such as call, delegatecall, or staticcall without checking the return value or failure status, allowing silent failure or malicious fallback exploitation. This includes failure to handle cases where the callee reverts, or where unchecked assumptions are made about the outcome of external code execution.",
            CategoryEnum.FRONT_RUNNING: "The outcome of a contract function can be influenced or manipulated by transaction ordering. This includes situations where a user's input or bid can be seen publicly (as a pending transaction for example) and acted upon before it is finalized, due to lack of commit-reveal or timing protections.",
            CategoryEnum.CONFIG_DEPENDENT: "The exploitability of the issue **entirely depends on** correct deployment or administrative configuration of parameters, addresses, or role assignments. If all privileged parties (e.g., owner, admin, deployer) act honestly and configure the contract correctly, the finding is not exploitable.",
            CategoryEnum.BUSINESS_LOGIC: "Discrepancies between the intended behavior of the contract—explicitly documented through comments, NatSpec, or external documentation, and the actual implementation found in the code. Business Logic findings highlight cases where the code diverges from the documented or expected protocol, potentially enabling unintended actions or restricting intended functionality.",
            CategoryEnum.PRECISION_LOSS: "Loss of numerical accuracy due to division or similar operations, where Solidity's integer division truncates results and can introduce rounding errors or underestimations. Precision Loss findings include any calculation where a lack of explicit rounding, scaling, or decimal handling leads to unexpected results or value loss.",
            CategoryEnum.CENTRALIZATION_RISK: "Identifies risks where a contract or protocol relies on a single or small group of privileged accounts (e.g., owner, admin) for critical operations. These are not typically vulnerabilities, as privileged roles are assumed to be trusted. However, they can be flagged to highlight unexpected and/or excessive centralization risks, or if it goes against the documentation or obvious protocol intent.",
            CategoryEnum.OTHER: "If the finding is not related to any of the above categories",
        }
        
        return description.get(category, description[CategoryEnum.OTHER])
    
    @classmethod
    def get_category_mitigation(cls, category: CategoryEnum) -> str:
        """
        Get category mitigation guidance.
        """
        mitigation = {
            CategoryEnum.REENTRANCY: """When analyzing reentrancy findings:
1. Only keep a reentrancy finding if **ALL** the following conditions are met:
   - The function is public or external
   - The function has no reentrancy guard
   - The function makes external calls to untrusted contracts
   - The function has some **state changes AFTER the external call**.
2. Mark as false positives if:
   - **The 3 conditions above are not met**
   - There is a ReentrancyGuard implementation
   - The CEI (Checks-Effects-Interactions) pattern is followed. Check = validations; Effects = state changes; Interactions = external calls.
   - There is no state changes after the external call
   - The call is internal and within the same contract
""",
            CategoryEnum.ACCESS_CONTROL: """When evaluating access control:
1. Consider context and trust assumptions:
   - Owner/admin roles are trusted by design
   - Distinguish between centralization risks and security vulnerabilities
2. Only keep access control findings if:
   - Privileged functions can be called by unauthorized users (sensitive states setter functions, etc.)
   - There's a clear exploit path with significant impact
   - It violates stated protocol assumptions
3. If the findings rather falls under centralization risks, mark as false positive unless:
   - It conflicts with documented decentralization goals
   - It enables critical protocol manipulation
   - It lacks time-locks or other safeguards where needed
""",
            CategoryEnum.INTEGER_OVERFLOW_UNDERFLOW: """When analyzing Solidity contracts version 0.8.0 and above, remember that arithmetic overflow and underflow checks are automatically included by the compiler. Only flag these as vulnerabilities if:
- The contract explicitly uses unchecked blocks
- There's a specific business requirement to handle the error case differently than a revert
- It's part of a more complex exploit chain
Otherwise, mark arithmetic checks as false positives that should be removed.
""",
            CategoryEnum.DENIAL_OF_SERVICE: "None Given",
            CategoryEnum.UNCHECKED_CALL: "None Given",
            CategoryEnum.FRONT_RUNNING: "None Given",
            CategoryEnum.CONFIG_DEPENDENT: "None Given",
            CategoryEnum.BUSINESS_LOGIC: "None Given",
            CategoryEnum.PRECISION_LOSS: "None Given",
            CategoryEnum.OTHER: "None Given",
            CategoryEnum.CENTRALIZATION_RISK: """When evaluating centralization risks:
1. By default, all centralization risk findings are considered informational and should be removed, as admin/owner roles are trusted by design.
2. Only keep a centralization risk finding if:
   - It directly contradicts documented decentralization goals or user expectations.
   - The centralized control can lead to a catastrophic failure or rug-pull scenario with no safeguards (like a time-lock).
   - The privilege is not clearly documented or is broader than necessary for its intended function.
""",
        }
        return mitigation.get(category, "None Given")


def infer_category(title: str, description: str) -> CategoryEnum:
    """
    Infer category from finding content based on title and description.
    """
    text = (title + " " + description).lower()
    
    if "unchecked" in text and ("return" in text or "call" in text):
        return CategoryEnum.UNCHECKED_CALL
    elif "reentrancy" in text or "re-entrant" in text:
        return CategoryEnum.REENTRANCY
    elif "denial" in text or "dos" in text or "unfillable" in text:
        return CategoryEnum.DENIAL_OF_SERVICE
    elif "access control" in text or "unauthorized" in text:
        return CategoryEnum.ACCESS_CONTROL
    elif "centralization" in text:
        return CategoryEnum.CENTRALIZATION_RISK
    elif "overflow" in text or "underflow" in text:
        return CategoryEnum.INTEGER_OVERFLOW_UNDERFLOW
    elif "precision" in text or "rounding" in text:
        return CategoryEnum.PRECISION_LOSS
    elif "front" in text and "run" in text:
        return CategoryEnum.FRONT_RUNNING
    elif "business logic" in text:
        return CategoryEnum.BUSINESS_LOGIC
    else:
        return CategoryEnum.OTHER

