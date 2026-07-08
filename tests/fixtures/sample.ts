function helper(x: number): number {
  return x + 1;
}

function main(y: number): number {
  return helper(y);
}

class Greeter {
  greet(name: string): string {
    return `hello ${name}`;
  }
}
