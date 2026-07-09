import glob
import os

from setuptools import find_packages, setup

package_name = 'intent_confidence'

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
    description='Mock 의도해석기 — confidence channel publisher (Phase 2b B2 검증).',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'publisher_node = intent_confidence.publisher_node:main',
            'estimator_node = intent_confidence.estimator_node:main',
        ],
    },
)
