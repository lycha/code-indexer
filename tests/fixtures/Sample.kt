package com.example

/**
 * A sample interface for greeting.
 */
interface Greeter {
    fun greet(name: String): String
}

/**
 * A sample class that implements Greeter.
 */
class SampleClass(val name: String) : Greeter {
    override fun greet(name: String): String {
        return "Hello, $name"
    }

    fun helper(): Int {
        return 42
    }
}

/**
 * A singleton object.
 */
object Registry {
    fun register(item: String): Boolean {
        return true
    }
}

fun topLevelFunction(x: Int): Int {
    return x * 2
}
