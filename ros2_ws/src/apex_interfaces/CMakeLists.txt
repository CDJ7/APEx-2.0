cmake_minimum_required(VERSION 3.8)
project(apex_interfaces)

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/OrderLine.msg"
  "msg/LineReceipt.msg"
  "msg/JobStatus.msg"
  "msg/JobEvent.msg"
  "msg/CellStatus.msg"
  "msg/ArmStatus.msg"
  "srv/SubmitJob.srv"
  "srv/ConfirmReceipt.srv"
  "srv/ConfirmReturn.srv"
  "srv/ResolveShortfall.srv"
  "srv/Operator.srv"
  "srv/WriteTag.srv"
  DEPENDENCIES builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
