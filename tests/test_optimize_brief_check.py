import pytest

from investment_assistant.optimize.brief_check import check_brief


def test_accepts_markdown_with_real_acceptance_section_verbatim():
    text = '# 实施 brief\n\n背景。\n\n## 4. 验收标准\n- 离线测试通过。\n'
    assert check_brief(text) == {'body': text}


@pytest.mark.parametrize('text', ['', ' \n ', None, 42])
def test_rejects_empty_or_nontext_brief(text):
    with pytest.raises(ValueError, match='brief_empty'):
        check_brief(text)


def test_length_limit_counts_original_text_not_stripped_text():
    prefix = '## 验收\n通过\n'
    text = prefix + '文' * (20000 - len(prefix))
    assert check_brief(text)['body'] == text
    with pytest.raises(ValueError, match='brief_length'):
        check_brief(text + ' ')


@pytest.mark.parametrize('text', ['提到验收两个字。', '# 背景\n以后再补验收。',
                                '```markdown\n## 验收\n通过\n```',
                                '<!--\n## 验收\n这只是隐藏的注释。\n-->',
                                '## 验收\n\n## 风险\n仍未定义验收。',
                                '## 无需验收\n通过'])
def test_requires_nonempty_acceptance_section_not_mention_or_code_example(text):
    with pytest.raises(ValueError, match='brief_acceptance_missing'):
        check_brief(text)


def test_accepts_setext_markdown_section():
    text = '验收\n----\n逐条检查指标。\n'
    assert check_brief(text)['body'] == text
