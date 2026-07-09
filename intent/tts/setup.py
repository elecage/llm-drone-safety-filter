from setuptools import find_packages, setup

setup(
    name='intent_tts',
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='LLM_Drone project',
    maintainer_email='elecage@users.noreply.github.com',
    description='TTS host pipeline — Piper + ask_user 음성 출력 + ROS 2 bridge (ADR-0016 D2).',
    license='Apache-2.0',
    tests_require=['pytest'],
)
