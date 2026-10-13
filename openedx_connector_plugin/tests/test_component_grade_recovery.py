"""Exercise production grade collectors without the full Open edX runtime.

Only ORM/modulestore boundaries are substituted. Both production collectors
execute unchanged, including grouping problem scores into subsection grades.
"""

import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'openedx_ai_connector' / 'student_insight.py'


class Rows:
    def __init__(self, rows, bank_rows=None):
        self.rows = rows
        self.bank_rows = bank_rows or []
        self.filter_calls = 0

    def filter(self, **kwargs):
        self.filter_calls += 1
        if 'module_state_key__in' in kwargs:
            return self.bank_rows
        return self.rows


def subsection(key, earned=10, possible=10, user_id=1, name=None):
    return SimpleNamespace(
        user_id=user_id, usage_key=key, earned_graded=earned,
        possible_graded=possible, modified='2026-10-12T10:00:00Z',
    )


def problem(key, earned=10, possible=10, user_id=1):
    return SimpleNamespace(
        student_id=user_id, module_state_key=key, grade=earned,
        max_grade=possible, modified='2026-10-12T10:00:00Z',
    )


class ComponentGradeRecoveryTests(unittest.TestCase):
    def test_course_index_tracks_selected_container_root_and_keeps_plain_problems_unrestricted(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        names = {'_materialize_course_learning_index', '_empty_completion_denominator_snapshot'}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        blocks = {
            'sequence': SimpleNamespace(kind='sequential', children=['bank', 'problem-6'], name='Quiz 5'),
            'bank': SimpleNamespace(kind='itembank', children=['container', 'problem-old'], max_count=1, name='Bank'),
            'container': SimpleNamespace(kind='vertical', children=['problem-5'], name='Questions'),
            'problem-5': SimpleNamespace(kind='problem', children=[], name='Question'),
            'problem-old': SimpleNamespace(kind='problem', children=[], name='Old question'),
            'problem-6': SimpleNamespace(kind='problem', children=[], name='Plain question'),
        }
        namespace = {
            'Any': object, '_safe_str': str,
            '_get_item_best_effort': lambda store, key: blocks[key],
            '_block_type': lambda block: block.kind,
            '_children_locations': lambda block: block.children,
            '_display_name': lambda block: block.name,
            '_looks_like_quiz_component': lambda name, block: False,
            '_STUDENTMODULE_FALLBACK_UNIT_TYPES': {'sequential'},
            '_CONTAINER_STUDENT_MODULE_TYPES': {'sequential', 'vertical', 'itembank'},
            '_STUDENTMODULE_FALLBACK_DENOMINATOR_TYPES': {'problem'},
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        store = SimpleNamespace(get_course=lambda key: SimpleNamespace(children=['sequence']))
        index = namespace['_materialize_course_learning_index']('course', store=store)
        self.assertEqual(index['itembanks']['bank'], {'children': ['container', 'problem-old'], 'max_count': 1})
        self.assertEqual(index['problem_to_itembank']['problem-5'], {'key': 'bank', 'selected_child': 'container'})
        self.assertNotIn('problem-6', index['problem_to_itembank'])
        self.assertEqual(index['problem_to_subsection']['problem-5']['key'], 'sequence')

    def collect(self, persistent_rows, problem_rows, *, persistent_available=True, planned=None, selection=None, itembank=False):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        names = {
            '_component_grade_snapshot', '_student_module_problem_grade_snapshot',
            '_subsection_problem_index',
            '_stored_itembank_grade_scope',
        }
        bank_rows = [] if selection is None else [SimpleNamespace(
            student_id=1, module_state_key='bank-5', state=json.dumps({'selected': selection}),
        )]
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        index = {
            'display_names': {'quiz-1': 'Quiz 1', 'quiz-5': 'Quiz 5', 'quiz-6': 'Quiz 6'},
            'problem_to_subsection': {
                'problem-1': {'key': 'quiz-1', 'name': 'Quiz 1', 'category': 'subsection'},
                'problem-5': {'key': 'quiz-5', 'name': 'Quiz 5', 'category': 'subsection'},
                'problem-6': {'key': 'quiz-6', 'name': 'Quiz 6', 'category': 'subsection'},
            },
        }
        if itembank:
            index['itembanks'] = {'bank-5': {'children': ['problem-5', 'problem-old'], 'max_count': 1}}
            index['problem_to_itembank'] = {
                key: {'key': 'bank-5', 'selected_child': key}
                for key in ['problem-5', 'problem-old']
            }
            index['problem_to_subsection']['problem-old'] = index['problem_to_subsection']['problem-5']
        persistent_manager = Rows(persistent_rows)
        problem_manager = Rows(problem_rows, bank_rows)
        persistent_model = SimpleNamespace(objects=persistent_manager) if persistent_available else None
        namespace = {
            'Any': object,
            'json': json,
            '_safe_str': lambda value: str(value) if value is not None else '',
            '_datetime_iso': lambda value: value,
            '_latest_datetime_iso': lambda left, right: max(left or '', right or ''),
            '_course_learning_index': lambda course: index,
            '_course_outline_quiz_components': lambda course: planned or [],
            '_persistent_subsection_grade_model': lambda: (persistent_model, '', None),
            '_student_module_model': lambda: (SimpleNamespace(objects=problem_manager), '', None),
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        course = SimpleNamespace(make_usage_key=lambda block_type, block_id: f'{block_type}-{block_id}')
        result = namespace['_component_grade_snapshot'](course, [SimpleNamespace(id=1)])
        self.assertLessEqual(persistent_manager.filter_calls, 1)
        self.assertGreaterEqual(problem_manager.filter_calls, 1)
        return {item['key']: item for item in result.get(1, [])}

    def test_partial_persistent_grades_do_not_hide_quiz_five_and_six(self):
        grades = self.collect(
            [subsection('quiz-1')],
            [problem('problem-1', earned=0), problem('problem-5'), problem('problem-6')],
        )

        self.assertEqual(set(grades), {'quiz-1', 'quiz-5', 'quiz-6'})
        self.assertEqual(grades['quiz-5']['percent'], 100)
        self.assertEqual(grades['quiz-6']['source'], 'StudentModule.problem_grade')
        self.assertEqual(grades['quiz-1']['percent'], 100)
        self.assertEqual(grades['quiz-1']['source'], 'PersistentSubsectionGrade')

    def test_valid_zero_persistent_grade_is_not_replaced_by_fallback(self):
        grades = self.collect([subsection('quiz-1', earned=0)], [problem('problem-1')])

        self.assertEqual(grades['quiz-1']['percent'], 0)
        self.assertEqual(grades['quiz-1']['source'], 'PersistentSubsectionGrade')

    def test_plan_identity_preserves_zero_actual_grade_and_does_not_mark_it_planned(self):
        grades = self.collect(
            [subsection('quiz-1', earned=0)], [],
            planned=[{'key': 'quiz-1', 'name': 'Quiz 1', 'quiz_number': 1,
                      'assessment_type': 'quiz', 'planned': True, 'percent': None}],
        )
        self.assertEqual(grades['quiz-1']['percent'], 0)
        self.assertEqual(grades['quiz-1']['source'], 'PersistentSubsectionGrade')
        self.assertFalse(grades['quiz-1']['planned'])
        self.assertEqual(grades['quiz-1']['assessment_type'], 'quiz')

    def test_null_persistent_score_is_filled_for_the_same_usage_key(self):
        grades = self.collect([subsection('quiz-5', earned=None)], [problem('problem-5', earned=8)])

        self.assertEqual(len(grades), 1)
        self.assertEqual(grades['quiz-5']['percent'], 80)
        self.assertEqual(grades['quiz-5']['source'], 'StudentModule.problem_grade')

    def test_zero_denominator_is_filled_without_division_by_zero(self):
        grades = self.collect([subsection('quiz-5', possible=0)], [problem('problem-5', earned=7)])

        self.assertEqual(grades['quiz-5']['percent'], 70)

    def test_missing_subsection_model_still_recovers_problem_scores(self):
        grades = self.collect([], [problem('problem-5')], persistent_available=False)

        self.assertEqual(grades['quiz-5']['percent'], 100)

    def test_unsubmitted_planned_quiz_does_not_acquire_a_fabricated_score(self):
        grades = self.collect(
            [subsection('quiz-1')], [],
            planned=[{'key': 'quiz-5', 'name': 'Quiz 5', 'percent': None, 'planned': True}],
        )

        self.assertEqual(set(grades), {'quiz-1', 'quiz-5'})
        self.assertIsNone(grades['quiz-5']['percent'])
        self.assertIsNone(grades['quiz-5'].get('earned'))
        self.assertTrue(grades['quiz-5']['planned'])

    def test_missing_final_test_does_not_create_a_final_grade(self):
        grades = self.collect([subsection('quiz-1')], [problem('problem-5')])

        self.assertEqual(set(grades), {'quiz-1', 'quiz-5'})
        self.assertFalse(any('final' in key for key in grades))

    def test_scores_are_merged_by_usage_key_not_display_label(self):
        grades = self.collect([subsection('quiz-1')], [problem('problem-5')])

        self.assertEqual(len(grades), 2)

    def test_old_deselected_itembank_problem_does_not_dilute_recovered_score(self):
        grades = self.collect(
            [subsection('quiz-1')], [problem('problem-5'), problem('problem-old', earned=0)],
            itembank=True, selection=[['problem', '5']],
        )
        self.assertEqual(grades['quiz-5']['percent'], 100)
        self.assertEqual(grades['quiz-5']['possible'], 10)

    def test_missing_or_stale_itembank_selection_does_not_create_a_recovered_grade(self):
        for selection in (None, [], [['problem', 'removed']], [['problem', '5'], ['problem', 'old']]):
            with self.subTest(selection=selection):
                grades = self.collect(
                    [subsection('quiz-1')], [problem('problem-5')],
                    itembank=True, selection=selection,
                )
                self.assertNotIn('quiz-5', grades)

    def test_selected_problem_without_saved_score_does_not_make_other_scores_complete(self):
        grades = self.collect(
            [subsection('quiz-1')], [problem('problem-old')],
            itembank=True, selection=[['problem', '5']],
        )
        self.assertNotIn('quiz-5', grades)


if __name__ == '__main__':
    unittest.main()
