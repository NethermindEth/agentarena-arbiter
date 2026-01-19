"""
Batch processing module for hierarchical processing of findings.
Implements optimal batch size calculation and hierarchical merging.
"""
import asyncio
import math
from collections.abc import Awaitable, Callable
from typing import TypeVar

import logging

logger = logging.getLogger(__name__)

# Configuration constants
MIN_BATCH_SIZE = 12
MAX_BATCHES = 8

T = TypeVar("T")
R = TypeVar("R")


async def process_in_batches(
    items: list[T],
    processor: Callable[[list[T]], Awaitable[list[R]]],
    batch_size: int,
    description: str = "items",
    hierarchical: bool = False,
) -> list[R]:
    """
    Process a list of items in batches using the provided processor function.

    Args:
        items: List of items to process
        processor: Async function that processes a batch of items
        batch_size: Size of each batch
        description: Description of the items for logging
        hierarchical: If True, uses hierarchical processing (pairs of results are merged and reprocessed)
                     This is necessary for operations like deduplication where items need to be compared
                     against each other across batches. For operations that process each item independently
                     (like mitigation and validation), hierarchical processing is not needed.

    Returns:
        List of processed items
    """
    if not items:
        return []

    # Calculate optimal number of batches
    num_batches = math.ceil(len(items) / batch_size)

    logger.info(
        f"[BatchProcessor] Processing {len(items)} {description} in {num_batches} batches (size: {batch_size})"
    )

    # Split items into batches
    batches = [items[i : i + batch_size] for i in range(0, len(items), batch_size)]

    # First level - process initial batches
    if hierarchical:
        # Run initial batch processing concurrently for hierarchical workflows
        for i, batch in enumerate(batches):
            logger.info(
                f"[BatchProcessor] Processing batch {i + 1}/{len(batches)} with {len(batch)} {description}"
            )
        first_level_results = await asyncio.gather(
            *[processor(batch) for batch in batches], return_exceptions=False
        )
    else:
        # Keep non-hierarchical flow unchanged (sequential)
        first_level_results = []
        for i, batch in enumerate(batches):
            logger.info(
                f"[BatchProcessor] Processing batch {i + 1}/{len(batches)} with {len(batch)} {description}"
            )
            processed_batch = await processor(batch)
            first_level_results.append(processed_batch)

    # If not hierarchical, just flatten the results
    if not hierarchical:
        flattened_results = []
        for batch_result in first_level_results:
            flattened_results.extend(batch_result)
        logger.info(
            f"[BatchProcessor] Completed processing {len(items)} {description}, returned {len(flattened_results)} results"
        )
        return flattened_results

    # For hierarchical processing, keep merging pairs until we have one final list
    current_level = first_level_results
    level = 1

    while len(current_level) > 1:
        next_level = []
        logger.info(
            f"[BatchProcessor] Hierarchical level {level}: processing {len(current_level)} result groups"
        )

        # Build tasks for pair-wise merges
        pair_tasks = []
        for i in range(0, len(current_level), 2):
            if i + 1 < len(current_level):
                merged = current_level[i] + current_level[i + 1]
                pair_tasks.append(processor(merged))
            else:
                # Odd one out - pass through
                next_level.append(current_level[i])

        if pair_tasks:
            pair_results = await asyncio.gather(*pair_tasks, return_exceptions=False)
            next_level.extend(pair_results)

        current_level = next_level
        level += 1

    final_results = current_level[0] if current_level else []
    logger.info(
        f"[BatchProcessor] Completed hierarchical processing of {len(items)} {description}, returned {len(final_results)} results"
    )

    return final_results


def calculate_optimal_batch_size(
    items_count: int,
    min_batch_size: int = MIN_BATCH_SIZE,
    max_batches: int = MAX_BATCHES,
) -> int:
    """
    Calculate the optimal batch size based on configuration settings.

    This function balances the need for efficient processing (fewer batches)
    with the constraints of LLM context windows (smaller batch sizes).

    The calculation ensures:
    1. Each batch has at least min_batch_size items (unless there are fewer items total)
    2. The number of batches doesn't exceed max_batches
    3. Items are distributed as evenly as possible across batches

    Args:
        items_count: Number of items to process
        min_batch_size: Minimum size of each batch (defaults to MIN_BATCH_SIZE)
        max_batches: Maximum number of batches to create (defaults to MAX_BATCHES)

    Returns:
        Optimal batch size
    """
    if items_count == 0:
        return min_batch_size

    if items_count < min_batch_size:
        return items_count

    # Calculate number of batches (capped at max_batches)
    num_batches = min(max_batches, (items_count + min_batch_size - 1) // min_batch_size)

    # Calculate batch size based on number of batches
    calculated_size = (items_count + num_batches - 1) // num_batches

    # Ensure we never return less than min_batch_size (unless items_count < min_batch_size)
    return max(calculated_size, min_batch_size)

