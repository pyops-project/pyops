export function inferExt(type: string): string {
    switch (type) {
        case 'task':
        case 'service':
        case 'library':
        case 'controller':
            return 'py';
        case 'view':
            return 'html';
        case 'markdown':
            return 'md';
        case 'jslibrary':
            return 'js';
        default:
            return '';
    }
}
