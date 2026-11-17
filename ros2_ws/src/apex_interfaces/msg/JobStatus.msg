# Orchestrator state, published at 2 Hz on /apex/job_status.
builtin_interfaces/Time stamp
uint32 job_id
string step               # see apex_orchestrator.job_machine.Step
float32 step_age_s
string destination
string route_direction    # clockwise | counter-clockwise | ""
uint32 parts_expected
uint32 parts_counted      # chute photoeye count
uint32[] queue
string fault_code
string fault_text
# robot (from the orchestrator's navigation port - same for Nav2 and the kinematic stand-in)
float32 robot_x
float32 robot_y
float32 robot_yaw
float32 battery_pct
bool docked
float32[] route_xy        # current route waypoints, flattened x0,y0,x1,y1,...
float32[] blocked_xy      # remembered corridor blockages, flattened
