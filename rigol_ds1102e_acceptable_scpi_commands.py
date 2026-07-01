#!/usr/bin/env python3

"""SCPI command formats accepted by the Rigol DS1102E MCP server."""


ACCEPTABLE_SCPI_COMMANDS: tuple[str, ...] = (
    # Identity
    "*IDN?",
    # Session
    ":AUTO",
    ":FORCetrig",
    ":RUN",
    ":STOP",
    ":TRIGger:STATus?",
    # Acquire
    ":ACQuire:AVERages?",
    ":ACQuire:AVERages {count}",
    ":ACQuire:MEMDepth?",
    ":ACQuire:MEMDepth {depth}",
    ":ACQuire:MODE?",
    ":ACQuire:MODE {mode}",
    ":ACQuire:SAMPlingrate? CHANnel{channel}",
    ":ACQuire:TYPE?",
    ":ACQuire:TYPE {acquire_type}",
    # Channel
    ":CHAN{channel}:COUP?",
    ":CHAN{channel}:COUP {coupling}",
    ":CHAN{channel}:DISP?",
    ":CHAN{channel}:DISP {state}",
    ":CHAN{channel}:OFFS?",
    ":CHAN{channel}:OFFS {offset}",
    ":CHAN{channel}:PROB?",
    ":CHAN{channel}:PROB {probe}",
    ":CHAN{channel}:SCAL?",
    ":CHAN{channel}:SCAL {scale}",
    # Timebase
    ":TIMebase:OFFSet?",
    ":TIMebase:OFFSet {offset}",
    ":TIMebase:SCALe?",
    ":TIMebase:SCALe {scale}",
    # Trigger
    ":TRIGger:HOLDoff?",
    ":TRIGger:HOLDoff {holdoff}",
    ":TRIGger:{mode}:LEVel?",
    ":TRIGger:{mode}:LEVel {level}",
    ":TRIGger:MODE?",
    ":TRIGger:MODE {mode}",
    ":TRIGger:{mode}:SOURce?",
    ":TRIGger:{mode}:SOURce {source}",
    ":TRIGger:{mode}:SWEep?",
    ":TRIGger:{mode}:SWEep {sweep}",
    # Measure
    ":MEASure:CLEar",
    ":MEASure:FREQuency? CHAN{channel}",
    ":MEASure:PERiod? CHAN{channel}",
    ":MEASure:VAVerage? CHAN{channel}",
    ":MEASure:VMAX? CHAN{channel}",
    ":MEASure:VMIN? CHAN{channel}",
    ":MEASure:VPP? CHAN{channel}",
    ":MEASure:VRMS? CHAN{channel}",
    # Waveform
    ":WAVeform:DATA? CHAN{channel}",
    ":WAVeform:POINts:MODE?",
    ":WAVeform:POINts:MODE {points_mode}",
)


__all__ = ["ACCEPTABLE_SCPI_COMMANDS"]
