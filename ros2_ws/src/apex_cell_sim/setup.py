"""ament_python setup for apex_cell_sim."""

from glob import glob

from setuptools import find_packages, setup

PACKAGE = "apex_cell_sim"

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
    description="APEx 2.0 simulated FlexBase cell (PLC handshake, KUKA, conveyor, gravity chute) with the real tag names and timing.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={"console_scripts": ["cell_sim = apex_cell_sim.server:main"]},
)
