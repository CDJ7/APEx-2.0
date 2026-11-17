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
