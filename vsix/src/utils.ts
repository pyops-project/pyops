export function inferExt(type: string): string {
    switch (type) {
        case 'task':
        case 'library':
        case 'controller':
            return 'py';
        case 'view':
            return 'html';
        case 'markdown':
            return 'md';
        default:
            return '';
    }
}
