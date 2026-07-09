from setuptools import find_packages, setup

setup(
    name='intent_loop',
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='LLM_Drone project',
    maintainer_email='elecage@users.noreply.github.com',
    description='명료화 인터랙션 루프 — 종료 정책(Φ_9/L4) + 오케스트레이터 (ADR-0016 D3, B4).',
    license='Apache-2.0',
    tests_require=['pytest'],
)
