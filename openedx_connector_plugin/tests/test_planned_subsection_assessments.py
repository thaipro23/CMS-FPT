"""Run the production index against the DOM1021 nested Quiz structure."""

import ast
from pathlib import Path
import re
from types import SimpleNamespace as Block
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'openedx_ai_connector' / 'student_insight.py'


def course_index(blocks, roots, *, course_error=None):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    names = {
        '_materialize_course_learning_index', '_empty_completion_denominator_snapshot',
        '_looks_like_quiz_component', '_quiz_numbers_from_label',
    }
    namespace = {
        'Any': object, 're': re, '_safe_str': lambda value: str(value or ''),
        '_get_item_best_effort': lambda store, key: blocks.get(key),
        '_block_type': lambda block: block.kind,
        '_children_locations': lambda block: block.children,
        '_display_name': lambda block: block.name,
        '_STUDENTMODULE_FALLBACK_UNIT_TYPES': {'sequential'},
        '_CONTAINER_STUDENT_MODULE_TYPES': {'chapter', 'sequential', 'vertical', 'itembank'},
        '_STUDENTMODULE_FALLBACK_DENOMINATOR_TYPES': {'problem'},
    }
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    def get_course(key):
        if course_error:
            raise course_error
        return Block(children=roots)
    store = Block(get_course=get_course)
    return namespace['_materialize_course_learning_index']('course', store=store)


class PlannedSubsectionAssessmentTests(unittest.TestCase):
    def test_dom1021_has_sixteen_quizzes_and_one_final_not_thirty_four_rows(self):
        # Production: 16 graded Quiz subsections, each with a Unit named Quiz,
        # plus Final test at both subsection and Unit levels.
        blocks = {}
        for number in range(1, 17):
            blocks[f'q{number}'] = Block(
                kind='sequential', name=f'Quiz {number}', graded=True,
                format='Quiz', children=[f'u{number}'],
            )
            blocks[f'u{number}'] = Block(kind='vertical', name='Quiz', children=[])
        blocks['final'] = Block(kind='sequential', name='Final test', graded=True,
                                format='Final test', children=['final-unit'])
        blocks['final-unit'] = Block(kind='vertical', name='Final test', graded=True, children=[])
        index = course_index(blocks, [*[f'q{i}' for i in range(1, 17)], 'final'])
        rows = index['planned_components']

        self.assertEqual(len(rows), 17)
        self.assertEqual([row['name'] for row in rows],
                         [*[f'Quiz {i}' for i in range(1, 17)], 'Final test'])
        self.assertEqual([row['usage_key'] for row in rows],
                         [*[f'q{i}' for i in range(1, 17)], 'final'])
        self.assertTrue(all(row['block_type'] == 'sequential' for row in rows))
        self.assertTrue(all(row['percent'] is None for row in rows))
        self.assertEqual(rows[-1]['assessment_type'], 'final_test')
        self.assertIsNone(rows[-1]['quiz_number'])
        self.assertEqual(index['completion_denominator']['subsection_total'], 17)
        self.assertIs(index.get('assessment_plan_complete'), True)

    def test_ungraded_quiz_and_scored_child_are_not_independent_assessments(self):
        blocks = {
            'practice': Block(kind='sequential', name='Quiz 17', graded=False,
                              format='Quiz', children=['child']),
            'child': Block(kind='vertical', name='Quiz 18', graded=True, children=['question']),
            'question': Block(kind='problem', name='Quiz 19', has_score=True, children=[]),
            'assignment': Block(kind='sequential', name='Assignment', graded=True,
                                format='Assignment', children=[]),
        }
        self.assertEqual(course_index(blocks, ['practice', 'assignment'])['planned_components'], [])

    def test_final_does_not_shift_unnumbered_graded_quizzes(self):
        blocks = {
            'final': Block(kind='sequential', name='Final test', graded=True,
                           format='Final test', children=[]),
            'first': Block(kind='sequential', name='Checkpoint', graded=True,
                           format='Quiz', children=[]),
            'second': Block(kind='sequential', name='Quiz', graded=True,
                            format='Quiz', children=[]),
        }
        rows = course_index(blocks, ['final', 'first', 'second'])['planned_components']
        self.assertEqual([(row['name'], row['quiz_number']) for row in rows],
                         [('Final test', None), ('Quiz 1', 1), ('Quiz 2', 2)])

    def test_course_without_final_does_not_invent_a_final(self):
        blocks = {'q': Block(kind='sequential', name='Quiz 5', graded=True,
                             format='Quiz', children=[])}
        rows = course_index(blocks, ['q'])['planned_components']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['quiz_number'], 5)
        self.assertEqual(rows[0].get('assessment_type'), 'quiz')

    def test_missing_descendant_marks_plan_incomplete_instead_of_authoritative(self):
        blocks = {'q': Block(kind='sequential', name='Quiz 1', graded=True,
                             format='Quiz', children=['unreadable-unit'])}
        index = course_index(blocks, ['q'])
        self.assertIs(index.get('assessment_plan_complete'), False)

    def test_course_lookup_error_never_marks_plan_complete(self):
        index = course_index({}, [], course_error=TimeoutError('modulestore timed out'))
        self.assertIs(index['assessment_plan_complete'], False)
        self.assertEqual(index['planned_components'], [])
        self.assertIn('TimeoutError', index['error'])

    def test_compact_learner_response_preserves_explicit_plan_completeness(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_student_learning_results_on_selected_database_uncached')
        user = Block(id=1, username='PH56940')
        for complete in (True, False):
            with self.subTest(complete=complete):
                namespace = {
                    'Any': object,
                    '_course_key_from_string': lambda course: course,
                    '_student_insight_user_map': lambda requested: {'ph56940': user},
                    '_enrollment_snapshot': lambda course, users: {1: {'status': 'enrolled'}},
                    '_persistent_grade_snapshot': lambda course, users: {},
                    '_component_grade_snapshot': lambda course, users: {1: []},
                    '_course_learning_index': lambda course: {'assessment_plan_complete': complete},
                    '_completion_snapshot': lambda course, users, **kwargs: ({}, 17),
                    '_compact_progress_payload': lambda progress: progress,
                    '_normalize_username_input': lambda value: value.lower(),
                }
                exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
                results = namespace[function.name]('course', [{'username': 'ph56940'}], compact=True)
                self.assertIs(results[0].get('assessment_plan_complete'), complete)


if __name__ == '__main__':
    unittest.main()
