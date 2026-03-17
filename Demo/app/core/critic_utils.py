"""
Utility functions for finding processing.
Includes contract code retrieval, path normalization, and finding filtering.
"""
from typing import Any, List, Dict
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Import ContractFile from contract_grouping
from app.core.contract_grouping import ContractFile


def is_interface_contract(contract_path: str) -> bool:
    """
    Checks if a contract path corresponds to an interface contract based on folder and filename conventions.

    Args:
        contract_path: The file path or name of the contract.

    Returns:
        True if the contract is considered an interface, False otherwise.
    """
    # Normalize path separators to forward slashes
    normalized_path = contract_path.replace("\\", "/")
    path_parts = normalized_path.split("/")
    filename = path_parts[-1]

    # Check if filename starts with 'I' followed by uppercase letter (interface convention)
    if len(filename) > 1 and filename[0] == 'I' and filename[1].isupper():
        return True

    # Check if path contains 'interface' folder
    if 'interface' in normalized_path.lower():
        return True

    return False


def filter_interfaces_findings(findings: List) -> List:
    """
    Filters out findings that are coming from interfaces.
    A finding is removed if ALL of its contracts are interfaces.

    Args:
        findings: List of findings (FindingDB objects)

    Returns:
        Filtered list of findings
    """
    filtered = []
    for finding in findings:
        file_paths = getattr(finding, 'file_paths', [])
        if not file_paths:
            # If no file_paths, keep the finding (can't determine if interface)
            filtered.append(finding)
            continue

        # Check if all contracts are interfaces
        all_interfaces = all(
            is_interface_contract(fp) for fp in file_paths
        )

        if not all_interfaces:
            filtered.append(finding)

    return filtered


def filter_findings_by_contracts_in_scope(
    findings: List,
    selected_contracts: List[str],
) -> List:
    """
    Filters findings to include only those in selected contracts.
    Uses contract name matching (filename stem).

    Args:
        findings: List of findings (FindingDB objects)
        selected_contracts: List of contract names (without extension) to filter by

    Returns:
        List of filtered findings
    """
    if not selected_contracts:
        return findings

    # Normalize selected contracts to set of stems (e.g., "Vault" from "Vault.sol")
    selected_stems = set()
    for contract in selected_contracts:
        stem = Path(contract).stem
        selected_stems.add(stem)

    filtered_findings = []
    for finding in findings:
        file_paths = getattr(finding, 'file_paths', [])
        if not file_paths:
            continue

        # Check if any file_path matches selected contracts
        finding_stems = {Path(fp).stem for fp in file_paths}
        if finding_stems.intersection(selected_stems):
            filtered_findings.append(finding)

    return filtered_findings


def get_finding_contract_code(finding: Any, contract_contents: Dict[str, ContractFile]) -> str:
    """
    Get contract code for a finding by looking up contracts in contract_contents.

    Args:
        finding: FindingDB object
        contract_contents: Dictionary mapping contract names to ContractFile objects

    Returns:
        Concatenated contract code string
    """
    finding_contract_code = ""
    
    # Get file_paths from finding
    file_paths = getattr(finding, 'file_paths', [])
    
    if not file_paths:
        # Fallback to Contracts field if available
        contracts = getattr(finding, 'Contracts', [])
        if contracts:
            for contract_name in contracts:
                if contract_name in contract_contents:
                    finding_contract_code += f"//--- File: {contract_name} ---\n"
                    finding_contract_code += contract_contents[contract_name].content
                    finding_contract_code += "\n\n"
    else:
        # Extract contract names from file paths and look up
        for fp in file_paths:
            contract_name = Path(fp).stem  # e.g., "Vault" from "src/Vault.sol"
            if contract_name in contract_contents:
                finding_contract_code += f"//--- File: {contract_name} ---\n"
                finding_contract_code += contract_contents[contract_name].content
                finding_contract_code += "\n\n"

    return finding_contract_code if finding_contract_code else "Not provided"


def assign_temporary_indexes(findings: List) -> List:
    """
    Ensure each finding has a temporary incremental index for deduplication.
    
    The index is stored as a temporary attribute on the finding object.
    This is needed for the deduplication process which uses indexes.

    Args:
        findings: List of findings (FindingDB objects)

    Returns:
        List of findings with assigned indexes
    """
    next_tmp_index = 0
    for f in findings:
        # Check if index already exists (as attribute)
        if not hasattr(f, 'index') or getattr(f, 'index', None) is None:
            # Assign temporary index as attribute
            f.index = next_tmp_index
            next_tmp_index += 1
    return findings


def strip_temporary_indexes(findings: List) -> List:
    """
    Remove temporary indexes that may have been assigned for deduplication.

    Args:
        findings: List of findings (FindingDB objects)

    Returns:
        List of findings with indexes removed
    """
    for f in findings:
        # Only clear if this object supports the attribute
        try:
            if hasattr(f, 'index'):
                f.index = None
        except Exception:
            # Be defensive: ignore objects that don't support setting index
            pass
    return findings


def coerce_findings_to_finding(findings: List[Any]) -> List:
    """
    Convert a heterogeneous list into a list of FindingDB objects.
    For agentarena-arbiter, we assume findings are already FindingDB objects.

    Args:
        findings: List of findings (should be FindingDB objects)

    Returns:
        List of FindingDB objects
    """
    # For agentarena-arbiter, we assume findings are already FindingDB objects
    # Just return them as-is
    return findings


def contract_paths_to_dict(contract_paths: List[str]) -> Dict[str, str]:
    """
    Converts a list of contract paths to a dictionary of basename to full path.
    
    Args:
        contract_paths: List of contract file paths
        
    Returns:
        Dictionary mapping basename to full path
    """
    result = {}
    for path in contract_paths:
        basename = Path(path).name
        result[basename] = path
    return result


def update_findings_paths(
    findings: List, 
    contract_paths: Dict[str, str]
) -> List:
    """
    Update contract paths in findings to full paths.
    Updates contract paths in findings to use full paths.
    
    Args:
        findings: List of findings (FindingDB objects)
        contract_paths: Dictionary mapping basename to full path
        
    Returns:
        List of findings with updated paths
    """
    # Build a case-insensitive lookup for basenames → full paths
    ci_lookup = {str(k).strip().lower(): v for k, v in contract_paths.items()}
    
    for finding in findings:
        file_paths = getattr(finding, 'file_paths', [])
        if not file_paths:
            continue
        
        updated_paths = []
        for fp in file_paths:
            # Normalize path separators before extracting basename
            normalized = fp.replace("\\", "/").strip()
            basename = Path(normalized).name
            # Try exact-case map first, then case-insensitive
            full_path = contract_paths.get(basename) or ci_lookup.get(basename.lower())
            if full_path:
                updated_paths.append(full_path)
            else:
                updated_paths.append(fp)
        finding.file_paths = updated_paths
    
    return findings


def filter_interfaces_findings_and_update_paths(
    findings: List,
    selected_contracts: List[str]
) -> List:
    """
    Filters out findings that are coming from interfaces AND updates contract paths.
    Filters out findings from interfaces and updates contract paths.
    
    Args:
        findings: List of findings (FindingDB objects)
        selected_contracts: List of contract paths
        
    Returns:
        Filtered list of findings with updated paths
    """
    # 1. Build contract_paths dict from selected_contracts
    contract_paths = contract_paths_to_dict(selected_contracts)
    
    # 2. Update all contract paths in findings
    findings = update_findings_paths(findings, contract_paths)
    
    # 3. Filter out interface findings
    findings = filter_interfaces_findings(findings)
    
    return findings

