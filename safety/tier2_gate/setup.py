import glob
import os

from setuptools import find_packages, setup

package_name = 'tier2_gate'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'),
            glob.glob(os.path.join('launch', '*.launch.py'))),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='LLM_Drone project',
    maintainer_email='elecage@users.noreply.github.com',
    description='티어2 계획 수준 검증 게이트 — G 결정 함수 + ROS 2 노드.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'gate_node = tier2_gate.gate_node:main',
            'px4_adapter = tier2_gate.px4_adapter_node:main',
            'dispatch_to_tier1 = tier2_gate.dispatch_to_tier1_node:main',
        ],
    },
)
