/**
 * A sample interface for greeting.
 */
interface Greeter {
    greet(name: string): string;
}

/**
 * A sample class that implements Greeter.
 */
class SampleClass implements Greeter {
    private name: string;

    constructor(name: string) {
        this.name = name;
    }

    greet(name: string): string {
        return `Hello, ${name}`;
    }

    helper(): number {
        return 42;
    }
}

function topLevelFunction(x: number): number {
    return x * 2;
}
