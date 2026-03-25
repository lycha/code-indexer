package com.example;

import java.util.List;

/**
 * A sample interface for repositories.
 */
interface Repository<T> {
    T findById(int id);
    List<T> findAll();
}

/**
 * A sample service class.
 */
public class SampleService {
    private String name;

    public SampleService(String name) {
        this.name = name;
    }

    /**
     * Gets the name.
     */
    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }
}

enum Status {
    ACTIVE,
    INACTIVE
}
