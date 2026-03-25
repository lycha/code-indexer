module Authentication
  # A sample user service class.
  class UserService
    attr_reader :name

    def initialize(name)
      @name = name
    end

    # Greets the user with a message.
    def greet(greeting = "Hello")
      "#{greeting}, #{@name}"
    end

    def self.create(name)
      new(name)
    end
  end
end

def standalone_function(x, y)
  x + y
end
