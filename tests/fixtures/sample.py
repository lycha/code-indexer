"""Sample module for testing the Python AST parser.

This module contains a class with methods and a standalone oversized function
to test cAST chunking.
"""


class Calculator:
    """A simple calculator class."""

    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    def subtract(self, a: int, b: int) -> int:
        """Subtract b from a."""
        return a - b


def helper_function(x: int) -> int:
    """A simple helper function."""
    return x * 2


def oversized_function(data: list) -> dict:
    """This function is intentionally large to exceed the token limit for cAST chunking.

    It processes data through multiple stages with extensive logic to ensure
    the token count exceeds 512 tokens when estimated via len(split()) * 1.3.
    """
    # Stage 1: Validate and preprocess the input data
    if not isinstance(data, list):
        raise TypeError("Expected a list of data items")
    if len(data) == 0:
        return {"status": "empty", "results": [], "metadata": {}}

    validated_items = []
    for item in data:
        if isinstance(item, dict):
            if "value" in item and "key" in item:
                validated_items.append({
                    "key": str(item["key"]),
                    "value": float(item["value"]),
                    "processed": False,
                    "stage": "validated"
                })
            else:
                validated_items.append({
                    "key": "unknown",
                    "value": 0.0,
                    "processed": False,
                    "stage": "default"
                })
        elif isinstance(item, (int, float)):
            validated_items.append({
                "key": f"numeric_{item}",
                "value": float(item),
                "processed": False,
                "stage": "converted"
            })
        else:
            validated_items.append({
                "key": str(item),
                "value": 0.0,
                "processed": False,
                "stage": "stringified"
            })

    # Stage 2: Transform and aggregate the data
    aggregated_results = {}
    for idx, item in enumerate(validated_items):
        key = item["key"]
        value = item["value"]
        if key in aggregated_results:
            aggregated_results[key]["sum"] += value
            aggregated_results[key]["count"] += 1
            aggregated_results[key]["values"].append(value)
            aggregated_results[key]["indices"].append(idx)
        else:
            aggregated_results[key] = {
                "sum": value,
                "count": 1,
                "values": [value],
                "indices": [idx],
                "min": value,
                "max": value,
                "average": value
            }

    # Stage 3: Calculate statistics for each group
    for key, group in aggregated_results.items():
        values = group["values"]
        group["min"] = min(values) if values else 0.0
        group["max"] = max(values) if values else 0.0
        group["average"] = group["sum"] / group["count"] if group["count"] > 0 else 0.0
        group["range"] = group["max"] - group["min"]
        sorted_values = sorted(values)
        n = len(sorted_values)
        if n % 2 == 0 and n > 0:
            group["median"] = (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2
        elif n > 0:
            group["median"] = sorted_values[n // 2]
        else:
            group["median"] = 0.0
        group["variance"] = sum((v - group["average"]) ** 2 for v in values) / max(group["count"], 1)
        group["std_dev"] = group["variance"] ** 0.5

    # Stage 4: Build the final result structure
    final_results = []
    for key, group in sorted(aggregated_results.items()):
        result_entry = {
            "key": key,
            "statistics": {
                "count": group["count"],
                "sum": group["sum"],
                "average": group["average"],
                "min": group["min"],
                "max": group["max"],
                "median": group["median"],
                "range": group["range"],
                "variance": group["variance"],
                "std_dev": group["std_dev"],
            },
            "raw_values": group["values"],
            "source_indices": group["indices"],
        }
        final_results.append(result_entry)

    # Stage 5: Generate metadata about the processing
    metadata = {
        "total_input_items": len(data),
        "validated_items": len(validated_items),
        "unique_groups": len(aggregated_results),
        "total_output_entries": len(final_results),
        "processing_stages": ["validation", "transformation", "aggregation", "statistics", "output"],
        "stage_descriptions": {
            "validation": "Input data validated and normalized to dict format",
            "transformation": "Items converted to standardized internal representation",
            "aggregation": "Items grouped by key with running totals",
            "statistics": "Statistical measures calculated per group",
            "output": "Final structured result assembled with metadata"
        }
    }

    return {
        "status": "success",
        "results": final_results,
        "metadata": metadata,
        "summary": {
            "groups_processed": len(final_results),
            "total_values_analyzed": sum(g["count"] for g in aggregated_results.values()),
            "global_min": min((g["min"] for g in aggregated_results.values()), default=0.0),
            "global_max": max((g["max"] for g in aggregated_results.values()), default=0.0),
        }
    }
