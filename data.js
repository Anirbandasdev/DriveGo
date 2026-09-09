const IMG = (id) => `https://images.unsplash.com/${id}?q=80&w=800&auto=format&fit=crop`;

const LOCATIONS = [
  {
    id: 1,
    name: "Kolkata",
    address: "Park Street Garage, Park Street",
    city: "Kolkata",
    cars: 18,
    bookings: 42,
    lat: 22.55,
    lng: 88.35,
    image: IMG("photo-1558431382-27e303142255")
  },
  {
    id: 2,
    name: "Salt Lake",
    address: "Salt Lake Rental Garage, Sector V",
    city: "Kolkata",
    cars: 14,
    bookings: 35,
    lat: 22.58,
    lng: 88.41,
    image: IMG("photo-1449824913935-59a10b8d2000")
  },
  {
    id: 3,
    name: "New Town",
    address: "New Town Garage, Action Area I",
    city: "Kolkata",
    cars: 10,
    bookings: 21,
    lat: 22.58,
    lng: 88.46,
    image: IMG("photo-1477959858617-67f85cf4f1df")
  },
  {
    id: 4,
    name: "Howrah",
    address: "Howrah Garage, Station Road",
    city: "Howrah",
    cars: 8,
    bookings: 15,
    lat: 22.58,
    lng: 88.31,
    image: IMG("photo-1474487548417-781cb71495f3")
  },
  {
    id: 5,
    name: "Kolkata Airport",
    address: "Airport Counter, Terminal 2",
    city: "Kolkata",
    cars: 12,
    bookings: 28,
    lat: 22.65,
    lng: 88.44,
    image: IMG("photo-1436491865332-7a61a109cc05")
  }
];

const CARS = [
  {
    id: 101,
    brand: "Toyota",
    model: "Innova Crysta",
    location: "Salt Lake",
    type: "SUV",
    pricePerDay: 2500,
    seats: 7,
    transmission: "Automatic",
    fuelType: "Diesel",
    status: "available",
    image: IMG("photo-1552519507-da3b142c6e3d"),
    features: ["AC", "5 Doors", "Cruise Control", "7 Airbags"],
    bookedSlots: []
  },
  {
    id: 102,
    brand: "Hyundai",
    model: "Creta",
    location: "Salt Lake",
    type: "SUV",
    pricePerDay: 1800,
    seats: 5,
    transmission: "Automatic",
    fuelType: "Petrol",
    status: "available",
    image: IMG("photo-1503376780353-7e6692767b70"),
    features: ["AC", "Sunroof", "Rear Camera"],
    bookedSlots: []
  },
  {
    id: 103,
    brand: "Kia",
    model: "Seltos",
    location: "New Town",
    type: "SUV",
    pricePerDay: 1900,
    seats: 5,
    transmission: "Manual",
    fuelType: "Diesel",
    status: "unavailable",
    image: IMG("photo-1494976388531-d1058494cdd8"),
    features: ["AC", "Sunroof"],
    bookedSlots: [{ from: "12 Sep, 10:00 AM", to: "15 Sep, 6:00 PM" }],
    nextFree: "16 Sep, 10:00 AM"
  },
  {
    id: 104,
    brand: "Mahindra",
    model: "XUV700",
    location: "Kolkata",
    type: "SUV",
    pricePerDay: 2200,
    seats: 7,
    transmission: "Automatic",
    fuelType: "Diesel",
    status: "available",
    image: IMG("photo-1502877338535-766e1452684a"),
    features: ["AC", "7 Seats", "ADAS"],
    bookedSlots: []
  },
  {
    id: 105,
    brand: "Tata",
    model: "Nexon",
    location: "Howrah",
    type: "Hatchback",
    pricePerDay: 1400,
    seats: 5,
    transmission: "Manual",
    fuelType: "Petrol",
    status: "available",
    image: IMG("photo-1549317661-bd32c8ce0db2"),
    features: ["AC", "5 Doors"],
    bookedSlots: []
  },
  {
    id: 106,
    brand: "Maruti Suzuki",
    model: "Brezza",
    location: "Kolkata Airport",
    type: "SUV",
    pricePerDay: 1600,
    seats: 5,
    transmission: "Automatic",
    fuelType: "Petrol",
    status: "unavailable",
    image: IMG("photo-1555215695-3004980ad54e"),
    features: ["AC", "Rear Camera"],
    bookedSlots: [{ from: "13 Sep, 9:00 AM", to: "14 Sep, 9:00 PM" }],
    nextFree: "15 Sep, 9:00 AM"
  }
];

const BOOKINGS = [
  {
    id: "DG-20260915-0012",
    car: "Toyota Innova Crysta",
    carImg: IMG("photo-1552519507-da3b142c6e3d"),
    location: "Salt Lake",
    pickup: "15 Sep, 10:00 AM",
    drop: "18 Sep, 6:00 PM",
    amount: 9204,
    status: "Confirmed",
    tab: "upcoming"
  },
  {
    id: "DG-20260902-0007",
    car: "Hyundai Creta",
    carImg: IMG("photo-1503376780353-7e6692767b70"),
    location: "New Town",
    pickup: "02 Sep, 9:00 AM",
    drop: "04 Sep, 7:00 PM",
    amount: 4250,
    status: "Completed",
    tab: "completed"
  },
  {
    id: "DG-20260820-0003",
    car: "Tata Nexon",
    carImg: IMG("photo-1549317661-bd32c8ce0db2"),
    location: "Howrah",
    pickup: "20 Aug, 10:00 AM",
    drop: "21 Aug, 6:00 PM",
    amount: 1980,
    status: "Cancelled",
    tab: "cancelled"
  }
];

const ADMIN = {
  stats: { totalCars: 62, availableCars: 44, activeBookings: 28, revenue: "₹4,85,300" },
  recent: [
    ["Aarav Sharma", "Innova Crysta", "Salt Lake", "15-18 Sep", "₹9,204", "Confirmed"],
    ["Priya Das", "Creta", "New Town", "14-16 Sep", "₹4,250", "Upcoming"],
    ["Rahul Verma", "Seltos", "New Town", "10-12 Sep", "₹4,480", "Completed"],
    ["Sneha Roy", "Nexon", "Howrah", "09-10 Sep", "₹1,980", "Cancelled"]
  ],
  availability: { Available: 44, Booked: 13, Maintenance: 5 }
};
