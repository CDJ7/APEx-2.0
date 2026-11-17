# FlexBase cell status from apex_plc_bridge (READ-ONLY view of the PLC), /apex/cell_status.
builtin_interfaces/Time stamp
bool connected
string backend            # logix | sim
int32 cell_mode           # 0 OFF, 1 MANUAL, 2 AUTO, 3 FAULT
bool safety_ok            # display only - never used to enable anything
bool safety_simulated     # true when the backend is apex_cell_sim
int32 transfer_mode       # 1 A, 2 B, 3 C
int32 chute_part_count
int32 parts_placed
bool load_complete
bool job_fault
int32 fault_code
string fault_text
bool stack_red
bool stack_amber
bool stack_green
