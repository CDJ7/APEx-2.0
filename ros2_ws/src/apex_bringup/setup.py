"""ament_python setup for apex_bringup."""

from glob import glob

from setuptools import find_packages, setup

PACKAGE = "apex_bringup"

setup(
    name=PACKAGE,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + PACKAGE]),
        ("share/" + PACKAGE, ["package.xml"]),
        ("share/" + PACKAGE + "/config", glob("config/*.yaml")),
        ("share/" + PACKAGE + "/worlds", glob("worlds/*.sdf")),
        ("share/" + PACKAGE + "/maps", glob("maps/*")),
        ("share/" + PACKAGE + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools", "PyYAML"],
    zip_safe=True,
    maintainer="APEx Systems team",
    maintainer_email="apex@example.com",
    description="APEx 2.0 lab model, generated Gazebo worlds and Nav2 maps, configuration and launch files.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={"console_scripts": ["generate_lab = apex_bringup.generate_lab:main",
                                      "align_map = apex_bringup.align_map:main"]},
)
