import { describe, it, expect } from 'vitest';
import { hasTextAfterToolCalls, type OutputItem } from './structuredOutput';

const text = (value: string): OutputItem => ({
	type: 'message',
	content: [{ type: 'output_text', text: value }]
});
const toolCall: OutputItem = { type: 'function_call', call_id: 'c1', name: 'create_documents' };
const toolResult: OutputItem = { type: 'function_call_output', call_id: 'c1' };

describe('hasTextAfterToolCalls', () => {
	it('waits while the model has only written text before its tool call', () => {
		expect(hasTextAfterToolCalls({ output: [text('I will make them.'), toolCall] })).toBe(false);
		expect(hasTextAfterToolCalls({ output: [text('I will make them.'), toolCall, toolResult] })).toBe(
			false
		);
	});

	it('is ready once text follows the last tool call', () => {
		expect(
			hasTextAfterToolCalls({ output: [text('Making them.'), toolCall, toolResult, text('Done')] })
		).toBe(true);
	});

	it('waits again after a second tool call and ignores empty text', () => {
		const output = [toolCall, toolResult, text('Part one'), toolCall, toolResult, text('  ')];
		expect(hasTextAfterToolCalls({ output })).toBe(false);
	});

	it('is ready when the message is done, even without text', () => {
		expect(hasTextAfterToolCalls({ output: [toolCall, toolResult], done: true })).toBe(true);
	});

	it('reads the text after the last tool block of a message that has no structured output', () => {
		const call = '<details type="tool_calls" done="true"><summary>Tool</summary></details>';
		expect(hasTextAfterToolCalls({ content: `Making them.\n${call}\n` })).toBe(false);
		expect(hasTextAfterToolCalls({ content: `Making them.\n${call}\nAll done.` })).toBe(true);
	});
});
