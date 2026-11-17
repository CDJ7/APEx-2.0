"""ament_python setup for apex_orchestrator."""

from glob import glob

from setuptools import find_packages, setup

PACKAGE = "apex_orchestrator"

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
    description="APEx 2.0 job orchestrator: job state machine, two-direction loop routing, Nav2/TurtleBot 4 adapter.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={"console_scripts": ["orchestrator = apex_orchestrator.orchestrator_node:main", "demo_client = apex_orchestrator.demo_client:main", "headless_demo = apex_orchestrator.demo:main"]},
)
