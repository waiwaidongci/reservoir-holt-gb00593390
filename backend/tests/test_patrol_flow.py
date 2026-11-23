"""巡查排班端到端流程测试（SQLite 内存库，不依赖 Postgres/Redis 连接）。"""

import unittest
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.main as main_module
from src.config.database import Base
from src.constants.enums import Role
from src.models.identity import User
from src.utils.security import hash_password


class PatrolFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy.pool import StaticPool
        cls.engine = create_engine('sqlite:///:memory:',
                                   connect_args={'check_same_thread': False},
                                   poolclass=StaticPool)
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(cls.engine, expire_on_commit=False)
        with cls.Session() as db:
            db.add(User(username='admin', role=Role.ADMIN, password_hash=hash_password('pw')))
            db.add(User(username='viewer', role=Role.VIEWER, password_hash=hash_password('pw')))
            db.commit()

        def override_connection():
            db = cls.Session()
            try:
                yield db
            finally:
                db.close()

        from src.config import database
        from src.controllers import auth
        from src.routes import patrol as patrol_routes
        main_module.engine = cls.engine
        main_module.app.dependency_overrides[database.connection] = override_connection
        main_module.app.dependency_overrides[auth.connection] = override_connection
        main_module.app.dependency_overrides[patrol_routes.connection] = override_connection
        cls.client = TestClient(main_module.app)
        resp = cls.client.post('/auth/login', json={'username': 'admin', 'password': 'pw'})
        cls.token = resp.json()['token']
        cls.h = {'Authorization': f'Bearer {cls.token}'}

    def _call(self, method, path, **kwargs):
        resp = getattr(self.client, method)(path, headers=self.h, **kwargs)
        self.assertIn(resp.status_code, (200, 201), resp.text)
        return resp.json()

    def _seed_network(self):
        # 三个池 + 值班室，全部启用，触线日 24/23/25。
        for code, name, trigger, dwell in [
            ('A', '一号池', '2026-11-24', 10),
            ('B', '二号池', '2026-11-23', 10),
            ('C', '三号池', '2026-11-25', 10),
        ]:
            self._call('post', '/patrol/pools', json={'code': code, 'name': name,
                       'trigger_date': trigger, 'dwell_min': dwell})
        for a, b, minutes in [('office', 'A', 10), ('office', 'B', 20), ('office', 'C', 30),
                              ('A', 'B', 15), ('B', 'C', 15), ('A', 'C', 40)]:
            self._call('post', '/patrol/edges', json={'node_a': a, 'node_b': b, 'travel_min': minutes})

    def test_full_flow(self):
        self._seed_network()
        shift = self._call('post', '/patrol/shifts', json={
            'shift_date': '2026-11-23', 'team': '白班',
            'start_clock': '08:00', 'end_clock': '12:00'})
        sid = shift['id']

        plan = self._call('post', f'/patrol/shifts/{sid}/plan')
        self.assertEqual(plan['state'], 'active')
        self.assertEqual(plan['version'], 1)
        self.assertTrue(plan['route']['feasible'])
        self.assertEqual(plan['route']['legs'][-1]['code'], 'office')

        # viewer 不可写。
        r = self.client.post('/auth/login', json={'username': 'viewer', 'password': 'pw'})
        vh = {'Authorization': f"Bearer {r.json()['token']}"}
        denied = self.client.post('/patrol/checkins', headers=vh, json={
            'shift_id': sid, 'pool_code': 'A', 'kind': 'arrive',
            'event_at': '2026-11-23T08:10:00+08:00'})
        self.assertEqual(denied.status_code, 403)

        # 开班：值班室出发，到 A，离开 A（固定已巡）。
        self._call('post', '/patrol/checkins', json={'shift_id': sid, 'pool_code': None,
                   'kind': 'depart', 'event_at': '2026-11-23T08:00:00+08:00', 'note': '出发'})
        self._call('post', '/patrol/checkins', json={'shift_id': sid, 'pool_code': 'A',
                   'kind': 'arrive', 'event_at': '2026-11-23T08:10:00+08:00'})
        self._call('post', '/patrol/checkins', json={'shift_id': sid, 'pool_code': 'A',
                   'kind': 'depart', 'event_at': '2026-11-23T08:20:00+08:00'})

        # 封路：office-B 路段封闭，触发重排。
        self._call('post', '/patrol/edges', json={'node_a': 'office', 'node_b': 'B',
                   'travel_min': 20, 'blocked': True, 'note': '施工封路'})
        new_plan = self._call('post', '/patrol/replan', json={'shift_id': sid, 'reason': 'office-B 封路'})
        self.assertEqual(new_plan['version'], 2)
        self.assertEqual(new_plan['state'], 'active')
        self.assertEqual(new_plan['route']['order'][0], 'A')  # 已巡 A 固定在最前
        # 旧版本被归档，不再 active。
        plans = self._call('get', f'/patrol/shifts/{sid}/plans')
        states = {p['version']: p['state'] for p in plans}
        self.assertEqual(states[1], 'archived')
        self.assertEqual(states[2], 'active')

        # 再封到无路可走：重排失败应回滚，旧 active 保持。
        for a, b in [('A', 'B'), ('B', 'C'), ('office', 'C'), ('A', 'C')]:
            self._call('post', '/patrol/edges', json={'node_a': a, 'node_b': b,
                       'travel_min': 60, 'blocked': True})
        failed = self.client.post('/patrol/replan', headers=self.h,
                                  json={'shift_id': sid, 'reason': '全面封路'})
        self.assertEqual(failed.status_code, 409)
        plans = self._call('get', f'/patrol/shifts/{sid}/plans')
        self.assertEqual([p for p in plans if p['state'] == 'active'][0]['version'], 2)

        # 解除封路继续签到 B、C 并返回。
        for a, b in [('A', 'B'), ('B', 'C'), ('office', 'C'), ('A', 'C')]:
            self._call('post', '/patrol/edges', json={'node_a': a, 'node_b': b,
                       'travel_min': 60, 'blocked': False})
        self._call('post', '/patrol/checkins', json={'shift_id': sid, 'pool_code': 'B',
                   'kind': 'arrive', 'event_at': '2026-11-23T08:40:00+08:00'})
        self._call('post', '/patrol/checkins', json={'shift_id': sid, 'pool_code': 'B',
                   'kind': 'depart', 'event_at': '2026-11-23T08:50:00+08:00'})

        # 并发两班同时提交签到：序号连续不漏记。
        r1 = self.client.post('/patrol/checkins', headers=self.h, json={
            'shift_id': sid, 'pool_code': 'C', 'kind': 'arrive',
            'event_at': '2026-11-23T09:10:00+08:00'})
        r2 = self.client.post('/patrol/checkins', headers=self.h, json={
            'shift_id': sid, 'pool_code': 'C', 'kind': 'depart',
            'event_at': '2026-11-23T09:20:00+08:00'})
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertNotEqual(r1.json()['seq'], r2.json()['seq'])

        self._call('post', '/patrol/checkins', json={'shift_id': sid, 'pool_code': None,
                   'kind': 'return', 'event_at': '2026-11-23T09:50:00+08:00', 'note': '按时返回'})

        checkins = self._call('get', f'/patrol/shifts/{sid}/checkins')
        seqs = [c['seq'] for c in checkins]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))  # 序号单调不漏

        report = self._call('get', f'/patrol/shifts/{sid}/handover')
        self.assertTrue(report['depart_at'])
        self.assertTrue(report['return_at'])
        self.assertEqual(report['checkin_count'], len(checkins))
        # B 触线日就是当天，按口径属于赶不及的池。
        missed_codes = {item['code'] for item in report['missed_pools']}
        self.assertIn('B', missed_codes)
        locations = [v['location'] for v in report['visits']]
        self.assertIn('office', locations)

    def test_invalid_shift_time_rejected(self):
        self._seed_network()
        resp = self.client.post('/patrol/shifts', headers=self.h, json={
            'shift_date': '2026-11-23', 'team': '夜班',
            'start_clock': '18:00', 'end_clock': '09:00'})
        self.assertEqual(resp.status_code, 422)

    def test_concurrent_checkins_get_distinct_sequential_ids(self):
        """两班各自持独立会话交错提交，序号必须连续且不覆盖。"""
        from src.services import patrol as patrol_service
        from src.utils.time import to_utc_naive
        self._seed_network()
        shift = self._call('post', '/patrol/shifts', json={
            'shift_date': '2026-11-23', 'team': '并发班',
            'start_clock': '08:00', 'end_clock': '12:00'})
        sid = shift['id']
        self._call('post', f'/patrol/shifts/{sid}/plan')

        stamp = datetime(2026, 11, 23, 8, 10)
        db1, db2 = self.Session(), self.Session()
        try:
            c1 = patrol_service.add_checkin(db1, sid, 'A', 'arrive', to_utc_naive(stamp), '甲班', None)
            c2 = patrol_service.add_checkin(db2, sid, 'B', 'arrive', to_utc_naive(stamp), '乙班', None)
            # 提交前各自分配到不同序号（依赖行锁；SQLite 串行化下也必须不重号）。
            self.assertNotEqual(c1.seq, c2.seq)
            db1.commit()
            db2.commit()
        finally:
            db1.close()
            db2.close()
        rows = self._call('get', f'/patrol/shifts/{sid}/checkins')
        seqs = sorted(row['seq'] for row in rows)
        self.assertEqual(seqs, [1, 2])
        actors = {row['actor'] for row in rows}
        self.assertEqual(actors, {'甲班', '乙班'})

    def test_replan_excludes_pool_where_crew_is_present(self):
        """到达某池尚未离开就重排：该池固定为接续点，不重复出现在待巡顺序。"""
        self._seed_network()
        shift = self._call('post', '/patrol/shifts', json={
            'shift_date': '2026-11-23', 'team': '在场班',
            'start_clock': '08:00', 'end_clock': '12:00'})
        sid = shift['id']
        self._call('post', f'/patrol/shifts/{sid}/plan')
        # 巡完 A（到达+离开），再到达 B 但不离开。
        self._call('post', '/patrol/checkins', json={'shift_id': sid, 'pool_code': None,
                   'kind': 'depart', 'event_at': '2026-11-23T08:00:00+08:00'})
        self._call('post', '/patrol/checkins', json={'shift_id': sid, 'pool_code': 'A',
                   'kind': 'arrive', 'event_at': '2026-11-23T08:10:00+08:00'})
        self._call('post', '/patrol/checkins', json={'shift_id': sid, 'pool_code': 'A',
                   'kind': 'depart', 'event_at': '2026-11-23T08:20:00+08:00'})
        self._call('post', '/patrol/checkins', json={'shift_id': sid, 'pool_code': 'B',
                   'kind': 'arrive', 'event_at': '2026-11-23T08:35:00+08:00'})
        new_plan = self._call('post', '/patrol/replan',
                              json={'shift_id': sid, 'reason': 'RW-08 触线日提前'})
        order = new_plan['route']['order']
        self.assertEqual(order[0], 'A')          # 已巡完的 A 固定
        self.assertEqual(order.count('B'), 0)    # 在场的 B 不重复排入
        self.assertIn('B', new_plan['route']['in_progress'])
        self.assertEqual(new_plan['route']['legs'][-1]['code'], 'office')


if __name__ == '__main__':
    unittest.main()
