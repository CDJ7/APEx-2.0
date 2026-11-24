"""ament_python setup for apex_rl."""

from glob import glob

from setuptools import find_packages, setup

PACKAGE = "apex_rl"

setup(
    name=PACKAGE,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + PACKAGE]),
        ("share/" + PACKAGE, ["package.xml"]),
    ],
    install_requires=["setuptools", "PyYAML", "numpy", "gymnasium"],
    zip_safe=True,
    maintainer="APEx Systems team",
    maintainer_email="apex@example.com",
    description="APEx 2.0 Learning-Based Navigation: 2D training environment from lab.yaml, baseline, evaluation and PPO training.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={"console_scripts": ["rl_evaluate = apex_rl.evaluate:main", "rl_train = apex_rl.train:main"]},
)
