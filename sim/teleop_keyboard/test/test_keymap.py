"""teleop_keyboard.teleop_keyboard_node 측 KEYMAP 단위 test.

termios 측 raw stdin 측 *직접 단위 test 어려움* — 본 test 측 *static keymap*
측 정합 + ENU frame 정합 + sign convention 측 검증.
"""

from __future__ import annotations

from teleop_keyboard.keymap import KEYMAP


class TestKeymap:
    def test_nine_keys_locked(self) -> None:
        """WASD + RF + QE + space = 9 키."""
        assert set(KEYMAP.keys()) == {'w', 's', 'a', 'd', 'r', 'f', 'q', 'e', ' '}

    def test_w_forward_positive_x(self) -> None:
        """W = forward = +x (ENU East)."""
        vx, vy, vz, omega = KEYMAP['w']
        assert vx == 1.0 and vy == 0.0 and vz == 0.0 and omega == 0.0

    def test_s_backward_negative_x(self) -> None:
        vx, vy, vz, omega = KEYMAP['s']
        assert vx == -1.0 and vy == 0.0 and vz == 0.0 and omega == 0.0

    def test_a_left_positive_y(self) -> None:
        """A = left = +y (ENU North) — 드론 측 East 측 향함 가정."""
        vx, vy, vz, omega = KEYMAP['a']
        assert vx == 0.0 and vy == 1.0 and vz == 0.0 and omega == 0.0

    def test_d_right_negative_y(self) -> None:
        vx, vy, vz, omega = KEYMAP['d']
        assert vx == 0.0 and vy == -1.0 and vz == 0.0 and omega == 0.0

    def test_r_up_positive_z(self) -> None:
        vx, vy, vz, omega = KEYMAP['r']
        assert vx == 0.0 and vy == 0.0 and vz == 1.0 and omega == 0.0

    def test_f_down_negative_z(self) -> None:
        vx, vy, vz, omega = KEYMAP['f']
        assert vx == 0.0 and vy == 0.0 and vz == -1.0 and omega == 0.0

    def test_q_yaw_left_positive_omega(self) -> None:
        """Q = yaw left = +omega_z (ENU 측 right-hand rule)."""
        vx, vy, vz, omega = KEYMAP['q']
        assert vx == 0.0 and vy == 0.0 and vz == 0.0 and omega == 1.0

    def test_e_yaw_right_negative_omega(self) -> None:
        vx, vy, vz, omega = KEYMAP['e']
        assert vx == 0.0 and vy == 0.0 and vz == 0.0 and omega == -1.0

    def test_space_zero_velocity(self) -> None:
        """space = stop (hover, zero velocity)."""
        vx, vy, vz, omega = KEYMAP[' ']
        assert vx == 0.0 and vy == 0.0 and vz == 0.0 and omega == 0.0

    def test_all_values_normalized(self) -> None:
        """모든 키 측 normalized (-1, 0, 1) 측 *정합* — 실 velocity 측 linear_speed
        / angular_speed scale 측 곱 시 *명확*.
        """
        for key, values in KEYMAP.items():
            for v in values:
                assert v in (-1.0, 0.0, 1.0), f"key={key!r} value={v} 측 normalized 측 위반"

    def test_no_diagonal_combinations(self) -> None:
        """1차 release — 각 키 측 *단일 축* 측만 active (diagonal 측 *연속 키* 측
        별 갱신 측). 본 invariant 측 *modal control* 측 단순성 측 보장.
        """
        for key, values in KEYMAP.items():
            non_zero = sum(1 for v in values if v != 0.0)
            assert non_zero <= 1, (
                f"key={key!r} 측 diagonal — {non_zero} axes active (max 1 허용)"
            )
