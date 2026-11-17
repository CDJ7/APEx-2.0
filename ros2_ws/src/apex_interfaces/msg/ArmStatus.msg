# KUKA KR C4 status as reported by the PLC (read-only), /apex/arm_status.
builtin_interfaces/Time stamp
int32 mode                # 1 T1, 2 T2, 3 AUT, 4 AUT EXT
string mode_name
bool ready
bool busy
bool fault
int32 program_no
