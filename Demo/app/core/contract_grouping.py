"""
Contract grouping module for processing findings by their associated contracts.
Groups findings by contract file for efficient batch processing.
"""
from collections.abc import Awaitable, Callable
from typing import Dict, List

import logging

logger = logging.getLogger(__name__)


# Contract file structure (simplified from ai-auditor)
class ContractFile:
    """Represents a contract file with its content and metadata."""
    def __init__(self, content: str, token_count: int = 0):
        self.content = content
        self.token_count = token_count


async def process_findings_by_contract_groups(
    findings: List,
    contract_contents: Dict[str, ContractFile],
    processor: Callable[[List], Awaitable[List]],
    operation_name: str = "processing",
    contract_language: str = "solidity",
) -> List:
    """
    Process findings by grouping them by their primary contract.

    This function:
    1. Groups findings by their primary contract (first contract in the file_paths list)
    2. For each group, processes the findings with the provided processor function
    3. Combines the results from all groups

    Args:
        findings: List of findings to process (FindingDB objects)
        contract_contents: Dictionary mapping contract filenames to ContractFile objects
        processor: Async function that processes a batch of Finding objects
        operation_name: Name of the operation for logging
        contract_language: Language of the contracts (default: "solidity")

    Returns:
        List of processed findings
    """
    if not findings:
        return []

    logger.info(
        f"[{operation_name.upper()}] Processing {len(findings)} findings using contract-based grouping"
    )

    # Group findings by first contract in their file_paths list
    grouped_findings, contracts_code = group_findings_by_contract(findings, contract_contents)

    logger.info(f"[{operation_name.upper()}] Processing {len(grouped_findings)} contract groups")

    # Process each contract group
    processed_findings: List = []

    for primary_contract, group_findings in grouped_findings.items():
        if not group_findings:
            continue

        contract_code = contracts_code.get(primary_contract, "")
        logger.info(
            f"[{operation_name.upper()}] Processing group for {primary_contract} with {len(group_findings)} findings"
        )
        # Process this group
        processed_group = await processor(group_findings)

        # Add processed groups to result
        processed_findings.extend(processed_group)

    logger.info(
        f"[{operation_name.upper()}] Contract-based processing complete: {len(processed_findings)} findings after processing"
    )
    return processed_findings


def group_findings_by_contract(
    findings: List, contract_contents: Dict[str, ContractFile]
) -> tuple[Dict[str, List], Dict[str, str]]:
    """
    Group findings by their primary contract and prepare contract code for each group.

    Args:
        findings: List of findings to group (FindingDB objects)
        contract_contents: Dictionary mapping contract filenames to ContractFile objects

    Returns:
        Tuple of:
        - Dictionary mapping primary contracts to their findings
        - Dictionary mapping primary contracts to their relevant code
    """
    # 1. Group findings by first contract in their file_paths list
    # Extract contract name from file path (e.g., "Vault" from "src/Vault.sol")
    grouped_by_contract: Dict[str, List] = {}
    
    for finding in findings:
        # Get file_paths from finding
        file_paths = getattr(finding, 'file_paths', [])
        if not file_paths:
            # If no file_paths, try to get from Contracts field if it exists
            contracts = getattr(finding, 'Contracts', [])
            if contracts:
                primary_contract = contracts[0]
            else:
                # Skip findings without contract information
                continue
        else:
            # Extract contract name from first file path
            from pathlib import Path
            primary_contract = Path(file_paths[0]).stem  # e.g., "Vault" from "src/Vault.sol"
        
        if primary_contract not in grouped_by_contract:
            grouped_by_contract[primary_contract] = []
        grouped_by_contract[primary_contract].append(finding)

    # 2. Extract code for each contract group
    contracts_code_by_group = {}
    for primary_contract, group_findings in grouped_by_contract.items():
        # Collect all unique contracts mentioned in this group
        all_contracts = set()
        for finding in group_findings:
            file_paths = getattr(finding, 'file_paths', [])
            if file_paths:
                from pathlib import Path
                for fp in file_paths:
                    contract_name = Path(fp).stem
                    all_contracts.add(contract_name)
            else:
                # Fallback to Contracts field if available
                contracts = getattr(finding, 'Contracts', [])
                all_contracts.update(contracts)

        # Extract code for all contracts in this group
        group_code = ""
        for contract in all_contracts:
            contract_file = contract_contents.get(contract)
            if not contract_file:
                continue
            contract_code = contract_file.content
            if contract_code:
                group_code += f"// File: {contract}\n"
                group_code += contract_code
                group_code += "\n\n"

        contracts_code_by_group[primary_contract] = group_code.strip()

    return grouped_by_contract, contracts_code_by_group

