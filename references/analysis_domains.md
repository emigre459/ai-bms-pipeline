# Analysis Agent Focus Areas

The following is a brief overview of common energy efficiency measures that analysts look for when reviewing commercial building systems. The analysis we create should ideally consider each of these or at least most of them when parsing the input BMS screenshots.

### **Equipment Scheduling**


Buildings waste significant energy by running HVAC equipment when spaces are unoccupied.
System operations should align with actual occupancy patterns. Common findings include units
that were never given an off-hours schedule, schedules that weren't updated when building use
changed, and units serving spaces with inherently low or intermittent occupancy.

### **Static Pressure**


HVAC systems are pressurized to push conditioned air out to occupied areas. Many systems
are overpressurized, designed for peak occupancy conditions even when actual demand is low.
A well-tuned static pressure strategy lowers the duct pressure in response to real-time demand,
allowing fans to slow down during low-load periods.

### **Supply Air Temperature**


HVAC systems are often configured with a fixed supply air temperature, designed for peak
cooling conditions. A better strategy raises the supply air temperature incrementally as cooling
demand decreases.

### **Hot Water Supply Temperature**


Heating plants often maintain a fixed hot water supply (HWS) temperature year-round,
calibrated for the coldest day. On mild days, this causes boilers to fire continuously to sustain
elevated water temperatures that no zone is actually requesting. An outdoor air temperature
(OAT) reset strategy addresses this by progressively lowering the HWS setpoint as outdoor
temperatures rise.

### **Economizer Control**


Economizers reduce air conditioning energy by admitting outdoor air when outdoor
temperatures are low enough to provide free cooling. Two distinct failure modes appear in
practice. The first is an economizer that is disabled, stuck, or misconfigured such that it fails to
take advantage of favorable outdoor conditions. The second is the opposite: an economizer that
fails to take into account humidity, letting in cool but damp outdoor air that requires significant
conditioning.

### **Fan System Balancing**


In large commercial buildings, multiple supply or return fans often operate in parallel, sharing a
common duct system. When these fans run at materially different speeds, faster fans push
against slower ones, generating turbulence and backpressure that wastes energy and
accelerates wear.


Page 3


### **Simultaneous Heating and Cooling**

One of the most wasteful patterns in commercial HVAC is heating and cooling at the same time.
At the zone level this sometimes happens when overcooled central air has to be heated back up
to avoid occupant discomfort. At the system level it appears as both heating and cooling plants
running simultaneously. Identifying simultaneous heating and cooling requires cross-referencing
supply air temperatures, zone reheat status, heating plant demand signals, and ambient
conditions, since the pattern can be obscured when each subsystem appears individually
reasonable.

### **Ventilation Optimization**


HVAC systems must meet minimum ventilation requirements, but many systems deliver
substantially more outdoor air than codes or occupancy require, particularly during partial
occupancy or off-hours periods. This excess outdoor air must be heated or cooled, representing
an avoidable conditioning load.
