import glob
import os

from setuptools import find_packages, setup

package_name = 'g2_waypoint_player'

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
        (os.path.join('share', package_name, 'scenarios'),
            glob.glob(os.path.join('scenarios', '*.yaml'))),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='LLM_Drone project',
    maintainer_email='elecage@users.noreply.github.com',
    description='G2 scripted waypoint player — YAML 시나리오 velocity sequence 재생.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'waypoint_player_node = g2_waypoint_player.waypoint_player_node:main',
        ],
    },
)
