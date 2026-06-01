#!/usr/bin/env python3

from collections.abc import Sequence

import numpy as np


TIME_SCALE_STEPS = (1.0, 2.0, 5.0)


def _as_int_array(samples: Sequence[int] | bytes | bytearray) -> np.ndarray:
    if isinstance(samples, (bytes, bytearray)):
        return np.frombuffer(samples, dtype=np.uint8).astype(np.int32)
    return np.asarray(samples, dtype=np.int32)


def normalize_waveform_samples(samples: Sequence[int]) -> list[int]:
    values = np.bitwise_xor(_as_int_array(samples), 0xFF)
    if values.size == 0:
        return []

    values = values - int(values.min())
    return values.tolist()


def detect_rising_edge_sample_indexes(
    samples: Sequence[int],
    threshold: int = 5,
    slope_threshold: int = 10,
) -> list[int]:
    values = _as_int_array(samples)
    filtered_indexes = np.flatnonzero(values > threshold)
    if filtered_indexes.size < 3:
        return []

    filtered_values = values[filtered_indexes]
    slopes = np.round(np.diff(filtered_values) / np.diff(filtered_indexes))
    if slopes.size < 2:
        return []

    edge_indexes = np.flatnonzero((slopes[:-1] >= slope_threshold) & (slopes[1:] < slope_threshold)) + 1
    return filtered_indexes[edge_indexes].tolist()


def normalize_and_detect_rising_edge_sample_indexes(
    clock_samples: Sequence[int],
    threshold: int = 5,
    slope_threshold: int = 10,
) -> list[int]:
    return detect_rising_edge_sample_indexes(
        normalize_waveform_samples(clock_samples),
        threshold=threshold,
        slope_threshold=slope_threshold,
    )


def sample_to_bit(
    sample: int,
    data_max: int,
    low_ratio: float = 0.2,
    high_ratio: float = 0.8,
) -> int:
    if sample > (high_ratio * data_max):
        return 0x1
    if sample < (low_ratio * data_max):
        return 0x0
    raise ValueError(f"sample value {sample} is between low/high thresholds for data_max={data_max}")


def decode_spi_data_words(
    data_samples: Sequence[int],
    sample_indexes: Sequence[int],
    low_ratio: float = 0.2,
    high_ratio: float = 0.8,
) -> dict[str, object]:
    if len(sample_indexes) % 32 != 0:
        raise ValueError(f"sample_indexes count {len(sample_indexes)} is not divisible by 32")

    values = _as_int_array(data_samples)
    if values.size == 0:
        raise ValueError("data_samples is empty")

    data_max = int(values.max())
    decoded_words: list[dict[str, int | str]] = []
    address_map: dict[int, dict[str, int | str]] = {}

    for group_start in range(0, len(sample_indexes), 32):
        data_word = 0
        group_indexes = sample_indexes[group_start : group_start + 32]

        for sample_index in group_indexes:
            index = int(sample_index)
            if index < 0 or index >= values.size:
                raise ValueError(f"sample index {index} is outside data_samples")

            bit = sample_to_bit(
                int(values[index]),
                data_max,
                low_ratio=low_ratio,
                high_ratio=high_ratio,
            )
            data_word = (data_word << 1) | bit

        address = data_word & 0x7
        decoded_word = {
            "word_index": len(decoded_words),
            "address": address,
            "value": data_word,
            "hex": f"0x{data_word:08X}",
        }
        decoded_words.append(decoded_word)
        address_map[address] = decoded_word

    return {
        "data_max": data_max,
        "sample_index_count": len(sample_indexes),
        "decoded_words": decoded_words,
        "address_map": {
            str(address): (address_map[address]["hex"] if address in address_map else None)
            for address in range(8)
        },
    }


def decoded_addresses(decoded: dict[str, object]) -> list[int]:
    decoded_words = decoded["decoded_words"]
    return [int(word["address"]) for word in decoded_words]  # type: ignore[index]


def validate_expected_addresses(decoded: dict[str, object], expected_addresses: Sequence[int] | None) -> None:
    if expected_addresses is None:
        return

    addresses = decoded_addresses(decoded)
    expected = [int(address) for address in expected_addresses]
    if addresses != expected:
        raise ValueError(f"decoded addresses {addresses} do not match expected {expected}")


def max2871_window_score(addresses: list[int], expected_addresses: Sequence[int] | None = None) -> int:
    score = 0
    if expected_addresses is not None and addresses == [int(address) for address in expected_addresses]:
        score += 1000
    if all(0 <= address <= 5 for address in addresses):
        score += 100
    if addresses == sorted(addresses, reverse=True):
        score += 50
    if addresses and addresses[-1] == 0:
        score += 25
    if len(set(addresses)) == len(addresses):
        score += 10
    return score


def decode_spi_data_words_windowed(
    data_samples: Sequence[int],
    sample_indexes: Sequence[int],
    expected_writes: int,
    max_extra_edges: int = 16,
    low_ratio: float = 0.2,
    high_ratio: float = 0.8,
    expected_addresses: Sequence[int] | None = None,
) -> dict[str, object]:
    expected_edges = expected_writes * 32
    extra_edges = len(sample_indexes) - expected_edges
    if extra_edges < 0:
        raise ValueError(f"sample_indexes count {len(sample_indexes)} is less than expected {expected_edges}")
    if extra_edges > max_extra_edges:
        raise ValueError(f"sample_indexes count {len(sample_indexes)} has {extra_edges} extra edges; limit is {max_extra_edges}")

    best: tuple[int, int, dict[str, object]] | None = None
    for start in range(extra_edges + 1):
        window = sample_indexes[start : start + expected_edges]
        try:
            decoded = decode_spi_data_words(
                data_samples,
                window,
                low_ratio=low_ratio,
                high_ratio=high_ratio,
            )
        except ValueError:
            continue

        addresses = decoded_addresses(decoded)
        if expected_addresses is not None and addresses != [int(address) for address in expected_addresses]:
            continue

        score = max2871_window_score(addresses, expected_addresses) - start
        if best is None or score > best[0]:
            best = (score, start, decoded)

    if best is None:
        raise ValueError("no valid decode window found")

    score, start, decoded = best
    decoded["window"] = {
        "enabled": True,
        "selected_start": start,
        "selected_stop": start + expected_edges,
        "expected_edges": expected_edges,
        "extra_edges": extra_edges,
        "score": score,
    }
    return decoded


def quantize_time_scale(
    value: float,
    minimum: float = 500e-9,
    maximum: float = 20e-6,
) -> float:
    if value <= minimum:
        return minimum
    if value >= maximum:
        return maximum

    decade = 1e-9
    while decade * 10.0 <= value:
        decade *= 10.0

    for step in TIME_SCALE_STEPS:
        candidate = step * decade
        if candidate >= value:
            return min(max(candidate, minimum), maximum)

    return min(max(10.0 * decade, minimum), maximum)


def propose_time_scale(
    current_time_scale: float,
    observed_edges: int,
    expected_edges: int,
    margin: float = 1.5,
    minimum: float = 500e-9,
    maximum: float = 20e-6,
) -> float:
    if observed_edges <= 0:
        return maximum

    raw_scale = current_time_scale * (float(expected_edges) / float(observed_edges)) * margin
    return quantize_time_scale(raw_scale, minimum=minimum, maximum=maximum)
