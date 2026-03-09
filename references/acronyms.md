# Purpose

This file specifies what different acronyms that may be present in images or YAML config files refer to.

# Acronyms and abbreviations

**BMS / BAS**
- **BAS:** Building Automation System — the controls (schedules, setpoints, sequences) that run the building; often used interchangeably with BMS in this repo.
- **BMS:** Building Management System — the system that monitors and controls building equipment (HVAC, lighting, etc.); data and screens from the BMS are the main input to this pipeline.

**HVAC and air systems**
- **CHWS:** Chilled Water Supply — cold water circulated to air-handling coils for cooling.
- **HWS:** Hot Water Supply — heated water circulated to coils and reheat for heating.
- **HVAC:** Heating, Ventilation, and Air Conditioning.
- **OA:** Outdoor Air — air brought in from outside (e.g. for ventilation or economizing).
- **OAT:** Outdoor Air Temperature.
- **RH:** Relative Humidity.
- **SA:** Supply Air — conditioned air delivered from the air-handling unit to the zones (e.g. SA static pressure, SA temperature).
- **VAV:** Variable Air Volume — zone terminals that modulate airflow; common signals include VAV heat request and VAV demand.
- **VFD:** Variable Frequency Drive — motor speed control for fans and pumps; often reported as speed or command (%).

**Analysis and outputs**
- **ECM:** Energy Conservation Measure — a discrete, actionable recommendation with quantified savings (used in `analysis-output.schema.yaml`).
- **EUI:** Energy Use Intensity — building energy consumption per unit of floor area (e.g. kBtu/ft²·yr).
- **LLM:** Large Language Model — used in this pipeline to drive energy efficiency analysis from BMS snapshots and screenshots.

**Emissions and units (from schema comments and factors)**
- **CO2e:** Carbon dioxide equivalent — emissions expressed as equivalent CO₂ (e.g. for different greenhouse gases).
- **eGRID:** EPA’s Emissions & Generation Resource Integrated Database — common source for grid emission factors (e.g. tCO2e/kWh).
- **FSP:** Fan Static Pressure — used in savings calcs (e.g. “FSP 0.65 W/cfm”).
- **HDD:** Heating Degree Days — used in heating savings estimates (e.g. HDD65).
- **inwc:** Inches water column — unit for static pressure (e.g. supply air static pressure).
- **kBtu:** Thousand Btu (British thermal units).
- **mlb:** Thousand pounds (of steam) — used for district steam in `analysis-output.schema.yaml`.
- **tCO2e:** Tons CO₂ equivalent — emissions in tons.
- **therm:** Unit of gas energy (e.g. natural gas rates and savings in therms).
