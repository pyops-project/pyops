import { errorText } from '../src/errorText';

const response = (status: number, statusText: string, body: Promise<unknown>) =>
    ({ status, statusText, json: () => body } as unknown as Response);

describe('errorText', () => {
    it('reports the status and the server message in detail', async () => {
        expect(await errorText(response(400, 'Bad Request', Promise.resolve({
            detail: "Invalid scriptlet name 'a.b': use only letters, digits, '_' and '-' (at least one character)",
        })))).toBe("400 Invalid scriptlet name 'a.b': use only letters, digits, '_' and '-' (at least one character)");
    });

    it('reports a validation error field by field', async () => {
        expect(await errorText(response(422, 'Unprocessable Entity', Promise.resolve({ detail: [
            { loc: ['body', 'max_retries'], msg: 'Input should be greater than or equal to 0' },
            { loc: ['body'], msg: 'Field required' },
        ] })))).toBe('422 max_retries: Input should be greater than or equal to 0; body: Field required');
    });

    it('reports the message of an error field', async () => {
        expect(await errorText(response(503, 'Service Unavailable', Promise.resolve({ error: 'No running service: s::svc' }))))
            .toBe('503 No running service: s::svc');
    });

    it('reports the status text for a body without a message', async () => {
        expect(await errorText(response(502, 'Bad Gateway', Promise.reject(new SyntaxError('not JSON')))))
            .toBe('502 Bad Gateway');
    });
});
