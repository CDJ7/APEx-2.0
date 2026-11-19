# Tilt-tray state from apex_tilt (on the TurtleBot), /apex/tilt/state at 10 Hz.
builtin_interfaces/Time stamp
string state              # UNKNOWN | FLAT | TILTING | RAMP | RETURNING | FAULT
string fault_text
uint32 cycles             # completed dump cycles since start-up
bool flat_switch
bool ramp_switch
bool robot_moving         # what the tray controller believes (from /odom)
