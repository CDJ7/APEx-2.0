cmake_minimum_required(VERSION 3.8)
project(apex_mapping NONE)  # no compiled code (adapted from aidanjmartin/tourbot's tourbot_navigation)

find_package(ament_cmake REQUIRED)

install(DIRECTORY config tools Connor_Room101 DESTINATION share/${PROJECT_NAME})
install(PROGRAMS scripts/teleop_map.py scripts/teleop_map.sh DESTINATION lib/${PROJECT_NAME})

ament_package()
