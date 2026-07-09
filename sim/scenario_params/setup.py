from setuptools import find_packages, setup

package_name = 'scenario_params'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='LLM_Drone project',
    maintainer_email='elecage@users.noreply.github.com',
    description='시나리오별 사용자 좌표 단일 진실 소스.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={},
)
