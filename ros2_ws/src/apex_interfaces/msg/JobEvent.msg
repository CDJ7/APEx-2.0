# Every job-machine event (also booked into inventory by StationCore), on /apex/job_events.
builtin_interfaces/Time stamp
string kind               # job_queued, plc_ack, part_counted, load_complete, receipt_confirmed, fault, ...
uint32 job_id
string data_json          # event details as JSON
