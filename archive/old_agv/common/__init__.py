"""Shared code used by every APEX AGV component.

* :mod:`common.config`         - loads ``config.yaml`` (single source of IPs/ports/tuning)
* :mod:`common.logging_setup`  - structured logging to console + rotating file
* :mod:`common.protocol`       - every wire protocol in the system:
    - :mod:`common.protocol.serial_protocol` Pi <-> Arduino line protocol
    - :mod:`common.protocol.messages`        station/PLC <-> AGV JSON messages
    - :mod:`common.protocol.modbus_map`      PLC Modbus TCP register map
    - :mod:`common.protocol.states`          shared enums (AGV state, order status)
* :mod:`common.netlink`        - newline-delimited-JSON framing helpers
"""
