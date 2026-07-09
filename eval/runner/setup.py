from setuptools import find_packages, setup

package_name = 'eval_runner'

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
    description='paper §C trial 격자 자동화 runner — ADR-0025 D3 1000 trial enumeration + 5 차원 seed 정책 + ablation chain invariant.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'eval-runner = eval_runner.runner:main',
            'eval-runner-one = eval_runner.run_one:main',
            'eval-aggregate = eval_runner.metrics_aggregator:main',
        ],
    },
)
