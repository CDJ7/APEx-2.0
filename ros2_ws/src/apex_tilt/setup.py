"""ament_python setup for apex_tilt."""

from glob import glob

from setuptools import find_packages, setup

PACKAGE = "apex_tilt"

setup(
    name=PACKAGE,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + PACKAGE]),
        ("share/" + PACKAGE, ["package.xml"]),
    ],
    install_requires=["setuptools", "PyYAML"],
    zip_safe=True,
    maintainer="APEx Systems team",
    maintainer_email="apex@example.com",
    description="APEx 2.0 tilting return tray on the TurtleBot 4 (controller, simulated and serial motor backends, ROS node).",
    license="MIT",
    tests_require=["pytest"],
    entry_points={"console_scripts": ["tilt_platform = apex_tilt.tilt_node:main"]},
)
